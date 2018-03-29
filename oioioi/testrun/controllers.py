import shutil
import tempfile
from zipfile import is_zipfile

from django import forms
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.base import ContentFile
from django.template.context import RequestContext
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from oioioi.base.utils.archive import Archive
from oioioi.contests.controllers import submission_template_context
from oioioi.contests.models import ScoreReport, Submission, SubmissionReport
from oioioi.evalmgr.tasks import extend_after_placeholder
from oioioi.programs.controllers import (ProgrammingContestController,
                                         ProgrammingProblemController)
from oioioi.programs.models import CompilationReport
from oioioi.programs.problem_instance_utils import \
    get_allowed_languages_extensions
from oioioi.testrun.models import TestRunProgramSubmission, TestRunReport


class TestRunProblemControllerMixin(object):
    """ProblemController mixin that adds testrun handlers to the recipe and
       adds testrun config to contest's admin panel.
    """

    def fill_evaluation_environ(self, environ, submission, **kwargs):
        if submission.kind != 'TESTRUN':
            return super(TestRunProblemControllerMixin, self) \
                .fill_evaluation_environ(environ, submission, **kwargs)
        # This *must be* called after that if above, we do not want
        # `generate_base_environ` to be called twice per environ.
        self.generate_base_environ(environ, submission, **kwargs)

        recipe_body = [
                ('make_test',
                    'oioioi.testrun.handlers.make_test'),
                ('run_tests',
                    'oioioi.programs.handlers.run_tests',),
                ('run_tests_end',
                    'oioioi.programs.handlers.run_tests_end'),
                ('grade_submission',
                    'oioioi.testrun.handlers.grade_submission'),
                ('make_report',
                    'oioioi.testrun.handlers.make_report'),
            ]
        extend_after_placeholder(environ, 'after_compile', recipe_body)

        environ['error_handlers'].append(('delete_output',
                'oioioi.testrun.handlers.delete_output'))

        environ['save_outputs'] = True
        environ['check_outputs'] = False
        environ['report_kinds'] = ['TESTRUN']

    def is_submissions_limit_exceeded(self, request, problem_instance, kind):
        if kind != 'TESTRUN':
            return (super(TestRunProblemControllerMixin, self)
                    .is_submissions_limit_exceeded(request,
                                                   problem_instance,
                                                   kind))

        test_runs_number = Submission.objects.filter(
            user=request.user,
            problem_instance__id=problem_instance.id,
            kind='TESTRUN').count()

        # We only check ProblemInstance-specific config if test runs are
        # enabled for the problem itself.
        if (hasattr(problem_instance.problem, 'test_run_config')
                and hasattr(problem_instance, 'test_run_config')):
            test_runs_limit = problem_instance.test_run_config.test_runs_limit
        else:
            test_runs_limit = settings.DEFAULT_TEST_RUNS_LIMIT

        return test_runs_number >= test_runs_limit > 0


    def mixins_for_admin(self):
        from oioioi.testrun.admin import TestRunProgrammingProblemAdminMixin
        return super(TestRunProblemControllerMixin, self) \
                .mixins_for_admin() + (TestRunProgrammingProblemAdminMixin,)


ProgrammingProblemController.mix_in(TestRunProblemControllerMixin)


class TestRunContestControllerMixin(object):
    """ContestController mixin that sets up testrun app for the contest.
    """

    def fill_evaluation_environ_post_problem(self, environ, submission):
        if submission.kind != 'TESTRUN':
            return super(TestRunContestControllerMixin, self) \
                    .fill_evaluation_environ_post_problem(environ, submission)

    def get_testrun_input_limit(self):
        return getattr(settings, 'TESTRUN_INPUT_LIMIT', 100 * 1024)

    def get_testrun_zipped_input_limit(self):
        return getattr(settings, 'TESTRUN_ZIPPED_INPUT_LIMIT', 50 * 1024)

    def get_testrun_unzipped_input_limit(self):
        return getattr(settings, 'TESTRUN_UNZIPPED_INPUT_LIMIT',
                       10 * 1024 * 1024)

    def adjust_submission_form(self, request, form, problem_instance):
        super(TestRunContestControllerMixin, self) \
            .adjust_submission_form(request, form, problem_instance)

        if form.kind != 'TESTRUN':
            return

        def validate_file_size(file):
            if (file.name.upper().endswith(".ZIP") and
                    file.size > self.get_testrun_zipped_input_limit()):
                raise ValidationError(
                    _("Zipped input file size limit exceeded."))
            elif file.size > self.get_testrun_input_limit():
                raise ValidationError(_("Input file size limit exceeded."))

        def validate_zip(file):
            if file.name.upper().endswith(".ZIP"):
                archive = Archive(file, '.zip')
                if len(archive.filenames()) != 1:
                    raise ValidationError(
                        _("Archive should have only 1 file inside.")
                    )
                if (archive.extracted_size()
                        > self.get_testrun_unzipped_input_limit()):
                    raise ValidationError(
                        _("Uncompressed archive is too big to be"
                          " considered safe.")
                    )
                # Extraction is safe, see:
                # https://docs.python.org/2/library/zipfile.html#zipfile.ZipFile.extract
                tmpdir = tempfile.mkdtemp()
                try:
                    # The simplest way to check validity
                    # All other are kinda lazy and don't check everything
                    archive.extract(tmpdir)
                # Zipfile has some undocumented exception types, we shouldn't
                # rely on those, thus we better catch all
                except Exception:
                    raise ValidationError(_("Archive seems to be corrupted."))
                finally:
                    shutil.rmtree(tmpdir)

        form.fields['input'] = forms.FileField(
            allow_empty_file=True,
            validators=[validate_file_size, validate_zip],
            label=_("Input"),
            help_text=mark_safe(
                _(
                    "Maximum input size is"
                    " <strong>%(input_size)d KiB</strong> or"
                    " <strong>%(zipped_size)d KiB</strong> zipped."
                    " Keep in mind that this feature does not provide"
                    " any validation of your input or output."
                ) % {
                    "input_size": self.get_testrun_input_limit() / 1024,
                    "zipped_size": self.get_testrun_zipped_input_limit() / 1024
                }
            )
        )

        form.fields['file'].help_text = _("Language is determined by the file"
                " extension. The following are recognized: %s, but allowed"
                " languages may vary. You can paste the code below instead of"
                " choosing file.") % (', '.join(
                    get_allowed_languages_extensions(problem_instance)))

        if 'kind' in form.fields:
            form.fields['kind'].choices = [('TESTRUN', _("Test run")), ]

    def create_testrun(self, request, problem_instance, form_data,
            commit=True, model=TestRunProgramSubmission):
        submission = model(
                user=form_data.get('user', request.user),
                problem_instance=problem_instance,
                kind='TESTRUN')
        submit_file = form_data['file']
        if submit_file is None:
            lang_exts = getattr(settings, 'SUBMITTABLE_EXTENSIONS', {})
            extension = lang_exts[form_data['prog_lang']][0]
            submit_file = ContentFile(form_data['code'],
                    '__pasted_code.' + extension)
        # pylint: disable=maybe-no-member
        submission.source_file.save(submit_file.name, submit_file)
        input_file = form_data['input']
        submission.input_file.save(input_file.name, input_file)
        if commit:
            submission.save()
            submission.problem_instance.controller.judge(submission)
        return submission

    def update_submission_score(self, submission):
        if submission.kind != 'TESTRUN':
            return super(TestRunContestControllerMixin, self) \
                .update_submission_score(submission)

        try:
            report = SubmissionReport.objects.filter(submission=submission,
                    status='ACTIVE', kind='TESTRUN').get()
            score_report = ScoreReport.objects.get(submission_report=report)
            submission.status = score_report.status
            submission.score = score_report.score  # Should be None
        except ObjectDoesNotExist:
            if SubmissionReport.objects.filter(submission=submission,
                    status='ACTIVE', kind='FAILURE'):
                submission.status = 'SE'
            else:
                submission.status = '?'
        submission.save()

    def update_report_statuses(self, submission, queryset):
        if submission.kind != 'TESTRUN':
            return super(TestRunContestControllerMixin, self) \
                .update_report_statuses(submission, queryset)

        self._activate_newest_report(submission, queryset,
                kind=['TESTRUN', 'FAILURE'])

    def can_see_submission_status(self, request, submission):
        if submission.kind != 'TESTRUN':
            return super(TestRunContestControllerMixin, self) \
                .can_see_submission_status(request, submission)

        return True

    def get_visible_reports_kinds(self, request, submission):
        return ['TESTRUN'] + super(TestRunContestControllerMixin, self) \
                .get_visible_reports_kinds(request, submission)

    def get_supported_extra_args(self, submission):
        if submission.kind != 'TESTRUN':
            return super(TestRunContestControllerMixin, self) \
                .get_supported_extra_args(submission)
        return {}

    def render_submission(self, request, submission):
        if submission.kind != 'TESTRUN':
            return super(TestRunContestControllerMixin, self) \
                    .render_submission(request, submission)

        sbm_testrun = submission.programsubmission. \
                      testrunprogramsubmission

        return render_to_string('testrun/submission-header.html',
            context_instance=RequestContext(request,
                {'submission': submission_template_context(request,
                    sbm_testrun),
                'supported_extra_args':
                    self.get_supported_extra_args(submission),
                'input_is_zip': is_zipfile(sbm_testrun.input_file)}))

    def _render_testrun_report(self, request, report, testrun_report,
            template='testrun/report.html'):
        score_report = ScoreReport.objects.get(submission_report=report)
        compilation_report = \
            CompilationReport.objects.get(submission_report=report)
        output_container_id_prefix = \
            request.is_ajax() and 'hidden_output_data_' or 'output_data_'

        input_is_zip = False
        if testrun_report:
            input_is_zip = is_zipfile(
                        testrun_report.submission_report.submission.
                        programsubmission.testrunprogramsubmission.
                        input_file)

        return render_to_string(template,
            context_instance=RequestContext(request, {
                'report': report, 'score_report': score_report,
                'compilation_report': compilation_report,
                'testrun_report': testrun_report,
                'output_container_id_prefix': output_container_id_prefix,
                'input_is_zip': input_is_zip}))

    def render_report(self, request, report, *args, **kwargs):
        if report.kind != 'TESTRUN':
            return super(TestRunContestControllerMixin, self) \
                .render_report(request, report, *args, **kwargs)

        # It may not exists when compilation error occurs
        try:
            testrun_report = TestRunReport.objects.get(
                    submission_report=report)
        except TestRunReport.DoesNotExist:
            testrun_report = None

        return self._render_testrun_report(request, report, testrun_report)

    def valid_kinds_for_submission(self, submission):
        if submission.kind != 'TESTRUN':
            return super(TestRunContestControllerMixin, self). \
                valid_kinds_for_submission(submission)

        assert submission.kind == 'TESTRUN'
        return ['TESTRUN']

ProgrammingContestController.mix_in(TestRunContestControllerMixin)
