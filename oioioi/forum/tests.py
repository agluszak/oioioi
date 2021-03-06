import re
from datetime import timedelta  # pylint: disable=E0611

from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.utils import timezone

from oioioi.base.tests import TestCase, fake_time
from oioioi.contests.models import Contest
from oioioi.forum.forms import PostForm
from oioioi.forum.models import Category, Post, Thread, Ban


def get_contest_with_forum():
    contest = Contest.objects.get()
    contest.controller_name = \
            'oioioi.contests.controllers.ContestController'
    contest.save()
    return contest


def get_contest_with_no_forum():
    contest = Contest.objects.get()
    contest.controller_name = \
            'oioioi.oi.controllers.OIOnsiteContestController'
    contest.save()
    return contest


class TestForum(TestCase):
    fixtures = ['test_users', 'test_contest']

    def setUp(self):
        delta = timedelta(days=3)
        self.now = timezone.now()
        self.future = self.now + delta
        self.past = self.now - delta

    def test_no_forum_menu(self):
        contest = get_contest_with_no_forum()

        self.client.login(username='test_user')
        url = reverse('default_contest_view',
                      kwargs={'contest_id': contest.id})
        response = self.client.get(url, follow=True)
        self.assertNotIn('Forum', response.content)

    def test_forum_menu(self):
        contest = get_contest_with_forum()

        self.client.login(username='test_user')
        url = reverse('default_contest_view',
                      kwargs={'contest_id': contest.id})
        response = self.client.get(url, follow=True)
        self.assertIn('Forum', response.content)

    def test_lock_forum_with_no_unlock_date(self):
        contest = get_contest_with_forum()
        forum = contest.forum
        self.client.login(username='test_user')
        url = reverse('default_contest_view',
                      kwargs={'contest_id': contest.id})
        with fake_time(self.now):
            # locked, no unlock date set
            forum.lock_date = self.past
            forum.visible = False
            forum.save()

            # locked & not visible, so user does not see forum
            response = self.client.get(url, follow=True)
            self.assertNotIn('Forum', response.content)
            url = reverse('forum', kwargs={'contest_id': contest.id})
            response = self.client.get(url, follow=True)
            self.assertEqual(403, response.status_code)
            self.assertEqual(True, forum.is_locked(self.now))

            forum.visible = True
            forum.save()
            # locked & visible, so user sees forum
            response = self.client.get(url, follow=True)
            self.assertIn('Forum', response.content)
            self.assertEqual(True, forum.is_locked(self.now))

    def test_lock_forum_with_unlock_date(self):
        contest = get_contest_with_forum()
        forum = contest.forum
        forum.lock_date = self.past
        forum.visible = False
        forum.unlock_date = self.future
        forum.save()
        self.client.login(username='test_user')
        url = reverse('default_contest_view',
                      kwargs={'contest_id': contest.id})
        with fake_time(self.now):
            response = self.client.get(url, follow=True)
            self.assertNotIn('Forum', response.content)
            self.assertEqual(True, forum.is_locked(self.now))

    def test_unlock_forum(self):
        # not visible but not locked either, so it should be visible..
        contest = get_contest_with_forum()
        forum = contest.forum
        url = reverse('default_contest_view',
                      kwargs={'contest_id': contest.id})
        self.client.login(username='test_user')
        with fake_time(self.now):
            forum.visible = False
            forum.lock_date = self.past
            forum.save()
            self.assertEqual(True, forum.is_locked(self.now))
            response = self.client.get(url, follow=True)
            self.assertNotIn('Forum', response.content)

            forum.unlock_date = self.past
            forum.save()
            self.assertEqual(False, forum.is_locked(self.now))
            response = self.client.get(url, follow=True)
            self.assertIn('Forum', response.content)


class TestCategory(TestCase):
    fixtures = ['test_users', 'test_contest']

    def setUp(self):
        delta = timedelta(days=3)
        self.now = timezone.now()
        self.future = self.now + delta
        self.past = self.now - delta
        self.contest = get_contest_with_forum()
        self.category = Category(forum=self.contest.forum, name='test_category')
        self.category.save()

    def test_add_new(self):
        self.client.login(username='test_user')
        self.client.get('/c/c/')  # 'c' becomes the current contest

        url = reverse('oioioiadmin:forum_category_add')
        response = self.client.get(url, follow=True)
        self.assertEqual(403, response.status_code)

        self.client.logout()
        self.client.login(username='test_admin')
        self.client.get('/c/c/')  # 'c' becomes the current contest

        response = self.client.get(url, follow=True)
        self.assertEqual(200, response.status_code)

    def test_no_thread(self):
        forum = self.contest.forum
        self.client.login(username='test_user')
        url = reverse('forum_category', kwargs={'contest_id': self.contest.id,
                                                'category_id': self.category.id})
        with fake_time(self.now):
            response = self.client.get(url, follow=True)
            # not locked, adding new thread possible
            self.assertIn('Add new thread', response.content)

            forum.lock_date = self.past
            forum.save()
            self.assertEqual(True, forum.is_locked(self.now))
            url = reverse('forum_category',
                          kwargs={'contest_id': self.contest.id,
                                  'category_id': self.category.id})
            response = self.client.get(url, follow=True)
            # locked, adding new thread not possible
            self.assertEqual(200, response.status_code)
            self.assertNotIn('Add new thread', response.content)


class TestThread(TestCase):
    fixtures = ['test_users', 'test_contest']

    def setUp(self):
        delta = timedelta(days=3)
        self.past = timezone.now() - delta
        self.contest = get_contest_with_forum()
        self.forum = self.contest.forum
        self.cat = Category(forum=self.forum, name='test_category')
        self.cat.save()
        self.thr = Thread(category=self.cat, name='test_thread')
        self.thr.save()
        self.user = User.objects.get(username='test_user')

    def try_to_remove_post(self, post):
        url = reverse('forum_post_delete', kwargs={'contest_id': self.contest.id,
                                                   'category_id': self.cat.id,
                                                   'thread_id': self.thr.id,
                                                   'post_id': post.id})
        return self.client.get(url, follow=True)

    def test_remove_posts(self):
        p0 = Post(thread=self.thr, content='test0', author=self.user,
                  add_date=self.past)
        p0.save()
        p1 = Post(thread=self.thr, content='test1', author=self.user)
        p1.save()
        p2 = Post(thread=self.thr, content='test2', author=self.user)
        p2.save()

        self.client.login(username='test_user')
        # user tries to remove post p1 but cannot (it is not last post)
        response = self.try_to_remove_post(p1)
        self.assertEqual(403, response.status_code)

        # user can remove p2 (last post, added by user)
        response = self.try_to_remove_post(p2)
        self.assertEqual(200, response.status_code)
        self.assertIn('Delete confirmation', response.content)
        p2.delete()

        # user tries to remove post p1 (and he can!)
        response = self.try_to_remove_post(p1)
        self.assertEqual(200, response.status_code)
        self.assertIn('Delete confirmation', response.content)
        p1.delete()

        # user tries to remove post p0 but can't (added earlier than 15min ago)
        response = self.try_to_remove_post(p0)
        self.assertEqual(403, response.status_code)


class TestPost(TestCase):
    fixtures = ['test_users', 'test_contest']

    def setUp(self):
        delta = timedelta(days=3)
        self.past = timezone.now() - delta
        self.contest = get_contest_with_forum()
        self.user = User.objects.get(username='test_user')
        self.forum = self.contest.forum
        self.cat = Category(forum=self.forum, name='test_category')
        self.cat.save()
        self.thr = Thread(category=self.cat, name='test_thread')
        self.thr.save()
        self.p = Post(thread=self.thr, content='Test post!',
                      author=self.user, add_date=self.past)
        self.p.save()

    def assertContainsReportOption(self, response):
        self.assertNotContains(response, 'This post was reported')
        self.assertContains(response, 'report')

    def assertContainsApproveOption(self, response):
        self.assertNotContains(response,
                               'This post was approved.')
        self.assertContains(response, 'approve')

    def test_report(self):
        self.client.login(username='test_user')
        url = reverse('forum_post_report',
                      kwargs={'contest_id': self.contest.id,
                              'category_id': self.cat.id,
                              'thread_id': self.thr.id,
                              'post_id': self.p.id})
        name = self.user.first_name
        surname = self.user.last_name
        response = self.client.post(url, follow=True)
        self.assertIn('This post was reported', response.content)
        self.client.login(username='test_admin')
        url = reverse('forum_thread', kwargs={'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.post(url, follow=True)

        reported_pattern = r"was reported\s*by\s*<a[^>]*>\s*%s %s\s*<\/a>" \
                           % (name, surname)
        self.assertTrue(re.search(reported_pattern, response.content))

    def test_approve_after_report(self):
        self.client.login(username='test_admin')
        url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                              'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.get(url, follow=True)
        self.assertContainsReportOption(response)
        self.assertContainsApproveOption(response)

        self.client.login(username='test_user')
        url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                              'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.get(url, follow=True)
        self.assertContainsReportOption(response)
        self.assertNotContains(response, 'approve')

        url = reverse('forum_post_report',
                      kwargs={'contest_id': self.contest.id,
                              'category_id': self.cat.id,
                              'thread_id': self.thr.id,
                              'post_id': self.p.id})
        response = self.client.post(url, follow=True)
        self.assertContains(response, 'This post was reported')

        self.client.login(username='test_admin')
        url = reverse('forum_post_approve',
                      kwargs={'contest_id': self.contest.id,
                              'category_id': self.cat.id,
                              'thread_id': self.thr.id,
                              'post_id': self.p.id})
        response = self.client.post(url, follow=True)
        self.assertContains(response, 'revoke approval')

        self.client.login(username='test_user')
        url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                              'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.get(url, follow=True)
        self.assertNotContains(response, 'report')
        self.assertContains(response,
                            'This post was approved.')
        self.assertNotContains(response, 'revoke approval')

        self.p.refresh_from_db()
        self.assertTrue(self.p.approved)
        self.assertFalse(self.p.reported)

    def test_approve_without_report(self):
        self.client.login(username='test_admin')
        url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                              'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.get(url, follow=True)
        self.assertContainsReportOption(response)
        self.assertContainsApproveOption(response)

        url = reverse('forum_post_approve',
                      kwargs={'contest_id': self.contest.id,
                              'category_id': self.cat.id,
                              'thread_id': self.thr.id,
                              'post_id': self.p.id})
        self.client.post(url, follow=True)

        self.client.login(username='test_user')
        url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                              'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.get(url, follow=True)
        self.assertNotContains(response, 'report')
        self.assertContains(response,
                            'This post was approved.')

        self.p.refresh_from_db()
        self.assertTrue(self.p.approved)
        self.assertFalse(self.p.reported)

    def test_report_after_approve(self):
        self.p.approved = True
        self.p.save()

        self.client.login(username='test_admin')
        url = reverse('forum_post_report',
                      kwargs={'contest_id': self.contest.id,
                              'category_id': self.cat.id,
                              'thread_id': self.thr.id,
                              'post_id': self.p.id})
        self.client.post(url)

        self.p.refresh_from_db()
        self.assertTrue(self.p.approved)
        self.assertFalse(self.p.reported)

        self.client.login(username='test_user')
        url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                              'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.get(url, follow=True)
        self.assertNotContains(response, 'report')
        self.assertContains(response,
                            'This post was approved.')

    def test_revoking_approval_after_edit(self):
        self.p.approved = True
        self.p.save()

        self.client.login(username='test_user')
        url = reverse('forum_post_edit',
                      kwargs={'contest_id': self.contest.id,
                              'category_id': self.cat.id,
                              'thread_id': self.thr.id,
                              'post_id': self.p.id})
        self.client.get(url, follow=True)

        self.p.refresh_from_db()
        self.assertTrue(self.p.approved)

        url = reverse('forum_post_edit',
                      kwargs={'contest_id': self.contest.id,
                              'category_id': self.cat.id,
                              'thread_id': self.thr.id,
                              'post_id': self.p.id})
        self.client.post(url, {'content': 'Test content'})

        self.p.refresh_from_db()
        self.assertFalse(self.p.approved)

    def test_admin_approval_edit(self):
        self.p.reported = True
        self.p.save()

        data = {
            'content': self.p.content,
            'thread': self.thr.id,
            'reported': self.p.reported,
            'approved': True
        }

        self.client.login(username='test_admin')
        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('oioioiadmin:forum_post_change', args=(self.p.id,))
        self.client.post(url, data)

        self.p.refresh_from_db()
        self.assertTrue(self.p.approved)
        self.assertFalse(self.p.reported)

        data['reported'] = True
        self.client.post(url, data)

        self.p.refresh_from_db()
        self.assertTrue(self.p.approved)
        self.assertFalse(self.p.reported)

    def test_admin_approve_action(self):
        self.p.reported = True
        self.p.save()

        data = {'_selected_action': (self.p.id, ),
                'action': 'approve_action'}

        self.client.login(username='test_admin')
        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('oioioiadmin:forum_post_changelist')
        self.client.post(url, data, follow=True)

        self.p.refresh_from_db()
        self.assertTrue(self.p.approved)
        self.assertFalse(self.p.reported)

    def test_admin_revoke_approval_action(self):
        self.p.approved = True
        self.p.save()

        data = {'_selected_action': (self.p.id, ),
                'action': 'revoke_approval_action'}

        self.client.login(username='test_admin')
        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('oioioiadmin:forum_post_changelist')
        self.client.post(url, data, follow=True)

        self.p.refresh_from_db()
        self.assertFalse(self.p.approved)
        self.assertFalse(self.p.reported)

    def test_revoke_approval(self):
        self.p.approved = True
        self.p.save()

        self.client.login(username='test_user')
        url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                              'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.get(url, follow=True)
        self.assertNotContains(response, 'revoke approval')

        self.client.login(username='test_admin')
        url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                              'category_id': self.cat.id,
                                              'thread_id': self.thr.id})
        response = self.client.get(url, follow=True)
        self.assertContains(response, 'revoke approval')

        url = reverse('forum_post_revoke_approval',
                      kwargs={'contest_id': self.contest.id,
                              'category_id': self.cat.id,
                              'thread_id': self.thr.id,
                              'post_id': self.p.id})
        response = self.client.post(url, follow=True)
        self.assertNotContains(response, 'revoke approval')

        self.p.refresh_from_db()
        self.assertFalse(self.p.approved)
        self.assertFalse(self.p.reported)


class TestBan(TestCase):
    fixtures = ['test_users', 'test_contest']

    def setUp(self):
        self.user = User.objects.get(username='test_user')
        self.user2 = User.objects.get(username='test_user2')
        self.contest = get_contest_with_forum()
        self.forum = self.contest.forum
        self.cat = Category(forum=self.forum, name='test_category')
        self.cat.save()
        self.ban = Ban(reason="Saying Ni in forum")
        self.ban.user = self.user
        self.ban.admin = User.objects.get(username='test_admin')
        self.ban.forum = self.forum
        self.ban.save()

    def test_report_post(self):
        thr = Thread(category=self.cat, name='test_thread')
        thr.save()
        p = Post(thread=thr, content='This post will be reported.',
                 author=self.user, add_date=timezone.now())
        p.save()
        self.client.login(username='test_user')
        url = reverse('forum_post_report', kwargs={'contest_id': self.contest.id,
                                                   'category_id': self.cat.id,
                                                   'thread_id': thr.id,
                                                   'post_id': p.id})
        response = self.client.post(url, follow=True)
        self.assertEqual(403, response.status_code)
        self.ban.delete()
        response = self.client.post(url, follow=True)
        self.assertEqual(200, response.status_code)

    def test_add_thread(self):
        self.client.login(username='test_user')
        self.assertEquals(0, Thread.objects.all().count())
        new_thread_url = reverse('forum_add_thread', kwargs={
                                 'contest_id': self.contest.id,
                                 'category_id': self.cat.id})
        self.client.post(new_thread_url,
                         {'name': "Test Thread",
                          'content': "lorem ipsum lorem ipsum!"})
        self.assertEquals(0, Thread.objects.all().count())
        self.ban.delete()
        self.client.post(new_thread_url,
                         {'name': "Test Thread",
                          'content': "lorem ipsum lorem ipsum!"})
        thread = Thread.objects.all()[0]
        self.assertEquals("Test Thread", thread.name)
        self.assertEquals(1, thread.count_posts())
        self.assertEquals("lorem ipsum lorem ipsum!", thread.last_post.content)
        self.assertEquals(User.objects.get(username='test_user'),
                          thread.last_post.author)

    def test_edit_post(self):
        thr = Thread(category=self.cat, name='test_thread')
        thr.save()
        p = Post(thread=thr, content='This post will be reported.',
                 author=self.user, add_date=timezone.now())
        p.save()
        self.client.login(username='test_user')
        edit_url = reverse('forum_post_edit', kwargs={'contest_id': self.contest.id,
                                                     'category_id': self.cat.id,
                                                     'thread_id': thr.id,
                                                     'post_id': p.id})
        self.assertEquals(403, self.client.get(edit_url).status_code)
        self.ban.delete()
        self.assertEquals(200, self.client.get(edit_url).status_code)

    def test_add_post(self):
        thr = Thread(category=self.cat, name='test_thread')
        thr.save()
        thread_url = reverse('forum_thread', kwargs={'contest_id': self.contest.id,
                                                     'category_id': self.cat.id,
                                                     'thread_id': thr.id})
        self.client.login(username='test_user')
        self.assertFalse(Post.objects.filter(author=self.user).exists())
        response = self.client.get(thread_url)
        self.assertNotIsInstance(response.context['form'], PostForm)

        self.client.post(thread_url, {'content': "lorem ipsum?"})
        self.assertFalse(Post.objects.filter(author=self.user).exists())

        self.ban.delete()

        response = self.client.get(thread_url)
        self.assertIsInstance(response.context['form'], PostForm)

        self.client.post(thread_url, {'content': "lorem ipsum?"})
        self.assertTrue(Post.objects.filter(author=self.user).exists())
        post = Post.objects.filter(author=self.user)[0]
        self.assertEquals("lorem ipsum?", post.content)
        self.assertEquals(self.user, post.author)

    def test_ban_view_without_removing_reports(self):
        self.ban.delete()
        thr = Thread(category=self.cat, name='test_thread')
        thr.save()
        p0 = Post(thread=thr, content='test0', author=self.user2,
                  reported=True, reported_by=self.user)
        p0.save()
        p1 = Post(thread=thr, content='test1', author=self.user2,
                  reported=True, reported_by=self.user)
        p1.save()
        p2 = Post(thread=thr, content='test2', author=self.user2)
        p2.save()
        p3 = Post(thread=thr, content='test2', author=self.user,
                  reported=True, reported_by=self.user2)
        p3.save()

        def check_reports():
            p0.refresh_from_db()
            p1.refresh_from_db()
            p2.refresh_from_db()
            p3.refresh_from_db()
            return [p0.reported, p1.reported, p2.reported, p3.reported]

        self.assertEquals([True, True, False, True], check_reports())

        self.client.login(username='test_admin')
        self.assertFalse(Ban.objects.exists())

        ban_url = reverse('forum_user_ban', kwargs={'contest_id': self.contest.id,
                                                    'user_id': self.user.id})

        self.client.post(ban_url, {'reason': 'Abuse'})
        self.assertEquals(1, Ban.objects.count())
        ban = Ban.objects.all()[0]
        self.assertEquals(self.user, ban.user)
        self.assertEquals('test_admin', ban.admin.username)
        self.assertEquals('Abuse', ban.reason)
        self.assertEquals(self.contest.forum, ban.forum)
        self.assertEquals([True, True, False, True], check_reports())
        ban.delete()

        self.client.post(ban_url, {'reason': 'Abuse', 'delete_reports': True})
        self.assertEquals(1, Ban.objects.count())
        ban = Ban.objects.all()[0]
        self.assertEquals(self.user, ban.user)
        self.assertEquals('test_admin', ban.admin.username)
        self.assertEquals('Abuse', ban.reason)
        self.assertEquals(self.contest.forum, ban.forum)
        self.assertEquals([False, False, False, True], check_reports())
