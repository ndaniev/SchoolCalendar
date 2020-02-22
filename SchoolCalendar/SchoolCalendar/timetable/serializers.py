from django.contrib.auth.models import User
from django.utils.translation import gettext as _

from rest_framework.serializers import HyperlinkedModelSerializer, ModelSerializer, Serializer, IntegerField, CharField,\
    DateField, SerializerMethodField, ValidationError, PrimaryKeyRelatedField, BooleanField

import datetime

from timetable.models import Teacher, Holiday, Stage, AbsenceBlock, Assignment, HoursPerTeacherInClass, HourSlot, \
    Course, Subject
from timetable import utils


class CourseYearOnlySerializer(Serializer):
    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass

    year = IntegerField()


class CourseSectionOnlySerializer(ModelSerializer):
    class Meta:
        model = Course
        fields = ['id', 'section', 'year']


class AbstractTimePeriodSerializer(ModelSerializer):
    """
    date_start and date_end are the actual extremes of the model interval
    start and end are the extremes of the intersection among the model interval and the period filtered

    For instance, if the model has a period from 4th January and 15th January, and the filtered period is 7-20th of
    January, then start and end are 7-15th of January.
    """
    start = SerializerMethodField()
    end = SerializerMethodField()

    def get_start(self, obj, *args, **kwargs):
        """
        :return: the maximum value among the beginning of the holiday, and the beginning of the filtered period
        """
        if self.context['request'].GET.get('from_date'):
            start = datetime.datetime.strptime(self.context['request'].GET.get('from_date'), '%Y-%m-%d').date()
        else:
            # No filter applied
            return obj.date_start
        start = start if start > obj.date_start else obj.date_start
        return start

    def get_end(self, obj, *args, **kwargs):
        """
        :return: the minimum value among the end of the holiday, and the end of the filtered period
        """
        if self.context['request'].GET.get('to_date'):
            end = datetime.datetime.strptime(self.context['request'].GET.get('to_date'), '%Y-%m-%d').date()
        else:
            # No filter applied
            return obj.date_end
        end = end if end < obj.date_end else obj.date_end
        return end


class TeacherSerializer(ModelSerializer):
    class Meta:
        model = Teacher
        fields = ['id', 'url', 'first_name', 'last_name', 'username', 'email', 'is_staff', 'school', 'notes']


class CourseSerializer(ModelSerializer):
    class Meta:
        model = Course
        fields = ['id', 'year', 'school', 'school_year', 'section']


class SubjectSerializer(ModelSerializer):
    class Meta:
        model = Subject
        fields = ['id', 'name', 'school', 'school_year']


class HolidaySerializer(AbstractTimePeriodSerializer):
    """
    Returns the holiday filtered in a given period.
    """
    class Meta:
        model = Holiday
        fields = ['start', 'end', 'date_start', 'date_end', 'name', 'school', 'school_year']


class StageSerializer(AbstractTimePeriodSerializer):
    """
    Stage Serializer with period filter
    """
    class Meta:
        model = Stage
        fields = ['start', 'end', 'date_start', 'date_end', 'name', 'course', 'school', 'school_year']


class HourSlotSerializer(ModelSerializer):
    """
    Serializer for Hour Slots. No period filter is required
    """
    class Meta:
        model = HourSlot
        fields = ['id', 'hour_number', 'starts_at', 'ends_at', 'school', 'school_year', 'day_of_week', 'legal_duration']


class HoursPerTeacherInClassSerializer(ModelSerializer):
    """
    Serializer for teachers
    """
    missing_hours = SerializerMethodField()
    missing_hours_bes = SerializerMethodField()
    teacher = TeacherSerializer()
    subject = SubjectSerializer()

    class Meta:
        model = HoursPerTeacherInClass
        fields = ['teacher', 'course', 'subject', 'school_year', 'school', 'hours', 'hours_bes', 'missing_hours',
                  'missing_hours_bes']

    def compute_total_hours_assignments(self, assignments, hours_slots):
        """
        In order to compute the total_hour_assignments for a teacher, we should merge assignments with the
        hour_slots in a left outer join fashion.
        Where there exists an hour slot for a given assignment, then we should use the 'legal_duration' field.
        Where there is no time_slot for it, we should use instead the actual duration of the assignment.
        :param assignments: the list of assignments for a given teacher, course, school_year, school, subject (bes can
                            be both True or False)
        :param hours_slots: the list of hour_slots for a given school and school_year
        :return: the total number of hours planned (both past and in the future) for a given teacher, course, school,
                 school_year, subject.
        """
        # Create a 3 dimensional map, indexed by day_of_week, starts_at, ends_at -> legal_duration
        map_hour_slots = {}
        for el in hours_slots:
            if el['day_of_week'] not in map_hour_slots:
                map_hour_slots[el['day_of_week']] = {}
            if el['starts_at'] not in map_hour_slots[el['day_of_week']]:
                map_hour_slots[el['day_of_week']][el['starts_at']] = {}
            if el['ends_at'] not in map_hour_slots[el['day_of_week']][el['starts_at']]:
                map_hour_slots[el['day_of_week']][el['starts_at']][el['ends_at']] = {}
            map_hour_slots[el['day_of_week']][el['starts_at']][el['ends_at']] = el['legal_duration']

        for el in assignments:
            if el["date__week_day"] in map_hour_slots and el['hour_start'] in map_hour_slots[el['date__week_day']] and \
                    el['hour_end'] in map_hour_slots[el['date__week_day']][el['hour_start']]:
                el['legal_duration'] = map_hour_slots[el['date__week_day']][el['hour_start']][el['hour_end']]
            else:
                el['legal_duration'] = datetime.datetime.combine(datetime.date.min, el['hour_end']) -\
                                       datetime.datetime.combine(datetime.date.min, el['hour_start'])

        total = datetime.timedelta(0)
        for el in assignments:
            total += el['legal_duration']
        return total

    def get_missing_hours(self, obj, *args, **kwargs):
        """
        Missing hours is computed over the hours the teacher needs to do for a given class,
        and the hours already planned in that class.
        :param obj:
        :param args:
        :param kwargs:
        :return:
        """
        # Get start_date and end_date parameters from url
        start_date = self.context.get('request').query_params.get('start_date')
        end_date = self.context.get('request').query_params.get('end_date')
        assignments = Assignment.objects.filter(teacher=obj.teacher,
                                                course=obj.course,
                                                subject=obj.subject,
                                                school=obj.school,
                                                school_year=obj.school_year,
                                                bes=False).values('date__week_day', 'hour_start', 'hour_end')
        # Filter in a time interval
        if start_date:
            assignments = assignments.filter(date__gte=start_date)
        if end_date:
            assignments = assignments.filter(date__lte=end_date)

        for el in assignments:
            el['date__week_day'] = utils.convert_weekday_into_0_6_format(el['date__week_day'])

        hours_slots = HourSlot.objects.filter(school=obj.school,
                                              school_year=obj.school_year).values("day_of_week", "starts_at",
                                                                                  "ends_at", "legal_duration")
        total = self.compute_total_hours_assignments(assignments, hours_slots)
        return obj.hours - int(total.seconds/3600)

    def get_missing_hours_bes(self, obj, *args, **kwargs):
            """
            Missing hours is computed over the hours the teacher needs to do for a given class,
            and the hours already planned in that class.
            :param obj:
            :param args:
            :param kwargs:
            :return:
            """
            start_date = self.context.get('request').query_params.get('start_date')
            end_date = self.context.get('request').query_params.get('end_date')

            assignments = Assignment.objects.filter(teacher=obj.teacher,
                                                    course=obj.course,
                                                    subject=obj.subject,
                                                    school=obj.school,
                                                    school_year=obj.school_year,
                                                    bes=True).values('date__week_day', 'hour_start', 'hour_end')

            # Filter in a time interval
            if start_date:
                assignments = assignments.filter(date__gte=start_date)
            if end_date:
                assignments = assignments.filter(date__lte=end_date)

            for el in assignments:
                el['date__week_day'] = utils.convert_weekday_into_0_6_format(el['date__week_day'])

            hours_slots = HourSlot.objects.filter(school=obj.school,
                                                  school_year=obj.school_year).values("day_of_week", "starts_at",
                                                                                      "ends_at", "legal_duration")
            total = self.compute_total_hours_assignments(assignments, hours_slots)

            return obj.hours_bes - int(total.seconds / 3600)


class AssignmentSerializer(ModelSerializer):
    """
    Serializer for teachers
    """
    teacher = TeacherSerializer(read_only=True)
    teacher_id = PrimaryKeyRelatedField(write_only=True, queryset=Teacher.objects.all(), source='teacher')
    subject = SubjectSerializer(read_only=True)
    subject_id = PrimaryKeyRelatedField(write_only=True, queryset=Subject.objects.all(), source='subject')
    hour_slot = SerializerMethodField(read_only=True)

    def __init__(self, *args, **kwargs):
        super(AssignmentSerializer, self).__init__(*args, **kwargs)
        self.user = self.context['request'].user

    def get_hour_slot(self, obj, *args, **kwargs):
        """
        TODO: should better add the hour slot as as a FK in the Assignment model
        Per each Assignment, it returns the corresponding HourSlot (if it exists), otherwise None
        The problem is that it makes a query for each instance of assignment!
        :param obj: the assignment instance
        :return:
        """
        el = HourSlot.objects.filter(
            day_of_week=obj.date.weekday(),
            starts_at=obj.hour_start,
            ends_at=obj.hour_end,
            school=obj.school,
            school_year=obj.school_year
        )
        if el:
            return el[0].id
        return None

    def validate(self, attrs):
        """
        Check whether the hour_start is <= hour_end
        :param attrs: the values to validate
        :return: attrs or raises ValidationError
        """
        if attrs['hour_start'] > attrs['hour_end']:
            raise ValidationError(_('The start hour field can\'t be greater than the end hour'))
        return attrs

    def validate_subject_id(self, value):
        """
        Check whether the course is in the school of the user logged.
        Somewhere else we should check that the user logged has enough permissions to do anything with a course.
        :return:
        """
        if utils.get_school_from_user(self.user) != value.school:
            raise ValidationError(_("The subject {} is not taught in this School ({}).").format(
                value, utils.get_school_from_user(self.user)
            ))
        return value

    def validate_course(self, value):
        """
        Check whether the course is in the school of the user logged.
        Somewhere else we should check that the user logged has enough permissions to do anything with a course.
        :return:
        """
        if utils.get_school_from_user(self.user) != value.school:
            raise ValidationError(_('The course {} is not taught in this School ({}).').format(
                value, utils.get_school_from_user(self.user)
            ))
        return value

    def validate_school(self, value):
        """
        Check whether the school is the correct one for the admin user logged.
        :param value:
        :return:
        """
        if utils.get_school_from_user(self.user) != value:
            raise ValidationError(_('The school {} is not a valid choice.').format(
                value
            ))
        return value

    def validate_teacher(self, value):
        """
        Check whether the teacher is in the school of the user logged.
        Somewhere else we should check that the user logged has enough permissions to do anything with a teacher.
        :return:
        """
        if utils.get_school_from_user(self.user) != value.school:
            raise ValidationError(_('The teacher {} does not teach in this School ({}).'.format(
                value, value.school
            )))
        return value

    class Meta:
        model = Assignment
        fields = '__all__'


class AbsenceBlockSerializer(ModelSerializer):
    teacher = TeacherSerializer()

    class Meta:
        model = AbsenceBlock
        fields = ['teacher', 'hour_slot', 'school_year', 'id']


class TeacherSubstitutionSerializer(ModelSerializer):
    has_hour_before = SerializerMethodField()
    has_hour_after = SerializerMethodField()
    substitutions_made_so_far = SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super(TeacherSubstitutionSerializer, self).__init__(*args, **kwargs)
        self.user = self.context['request'].user
        # We have already checked in view .get_queryset whether the Assignment exists.
        self.assignment_to_substitute = Assignment.objects.get(id=self.context['request'].assignment_pk)

    class Meta:
        model = Teacher
        fields = ['school', 'notes', 'has_hour_before', 'has_hour_after', 'substitutions_made_so_far', 'first_name',
                  'last_name', 'id']

    def get_substitutions_made_so_far(self, obj, *args, **kwargs):
        return Assignment.objects.filter(teacher=obj.id,
                                         school=obj.school,
                                         school_year=self.assignment_to_substitute.school_year,
                                         substitution=True).count()

    def get_has_hour_after(self, obj, *args, **kwargs):
        related_hour_slot = HourSlot.objects.filter(starts_at=self.assignment_to_substitute.hour_start,
                                                    ends_at=self.assignment_to_substitute.hour_end,
                                                    day_of_week=self.assignment_to_substitute.date.weekday(),
                                                    school=obj.school,
                                                    school_year=self.assignment_to_substitute.school_year).first()
        if not related_hour_slot:
            # If there is not a related hour slot, then we are talking about a non standard assignment.
            # We return False by default
            return False
        if related_hour_slot.hour_number == max(HourSlot.objects.filter(
                                                    day_of_week=self.assignment_to_substitute.date.weekday(),
                                                    school=obj.school,
                                                    school_year=self.assignment_to_substitute.school_year)
                                                        .values_list('hour_number')[0]):
            # It is the last hour of the day, the teacher can't be at school after.
            return False
        later_hour_slot = HourSlot.objects.filter(school=obj.school,
                                                  school_year=self.assignment_to_substitute.school_year,
                                                  hour_number=related_hour_slot.hour_number + 1,
                                                  day_of_week=self.assignment_to_substitute.date.weekday()).first()
        if not later_hour_slot:
            # There is no later hour slot, therefore we can't say.
            return False

        return Assignment.objects.filter(teacher=obj,
                                         date=self.assignment_to_substitute.date,
                                         school=obj.school,
                                         school_year=self.assignment_to_substitute.school_year,
                                         hour_start=later_hour_slot.starts_at,
                                         hour_end=later_hour_slot.ends_at).exists()

    def get_has_hour_before(self, obj, *args, **kwargs):
        related_hour_slot = HourSlot.objects.filter(starts_at=self.assignment_to_substitute.hour_start,
                                                    ends_at=self.assignment_to_substitute.hour_end,
                                                    day_of_week=self.assignment_to_substitute.date.weekday(),
                                                    school=obj.school,
                                                    school_year=self.assignment_to_substitute.school_year).first()
        if not related_hour_slot:
            # If there is not a related hour slot, then we are talking about a non standard assignment.
            # We return False by default
            return False
        if related_hour_slot.hour_number == 1:
            # It is the first hour of the day, the teacher can't be at school before.
            return False
        previous_hour_slot = HourSlot.objects.filter(school=obj.school,
                                                     school_year=self.assignment_to_substitute.school_year,
                                                     hour_number=related_hour_slot.hour_number-1,
                                                     day_of_week=self.assignment_to_substitute.date.weekday()).first()
        if not previous_hour_slot:
            # There is no previous hour slot, therefore we can't say.
            return False

        return Assignment.objects.filter(teacher=obj,
                                         date=self.assignment_to_substitute.date,
                                         school=obj.school,
                                         school_year=self.assignment_to_substitute.school_year,
                                         hour_start=previous_hour_slot.starts_at,
                                         hour_end=previous_hour_slot.ends_at).exists()
