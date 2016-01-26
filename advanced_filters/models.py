from django.conf import settings
from django.db import models
from django.db.models import Q, get_model, FieldDoesNotExist
from django.db.models.fields import DateField
from django.contrib.admin.util import get_fields_from_path
from django.utils.translation import ugettext_lazy as _

from .q_serializer import QSerializer


class UserLookupManager(models.Manager):
    def filter_by_user(self, user):
        """All filters that should be displayed to a user (by users/group)"""

        return self.filter(Q(users=user) | Q(groups__in=user.groups.all()))


class AdvancedFilter(models.Model):
    class Meta:
        verbose_name = _('Advanced Filter')
        verbose_name_plural = _('Advanced Filters')

    title = models.CharField(max_length=255, null=False, blank=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   related_name='created_advanced_filters')
    url = models.CharField(max_length=255, null=False, blank=False)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True)
    groups = models.ManyToManyField('auth.Group', blank=True)

    objects = UserLookupManager()

    b64_query = models.CharField(max_length=2048)
    model = models.CharField(max_length=64, blank=True, null=True, choices=())

    FIELD_CHOICES = (
        ("_OR", _("OR (mark an or between blocks)")),
    )

    @property
    def query(self):
        """
        De-serialize, decode and return an ORM query stored in b64_query.
        """
        if not self.b64_query:
            return None
        s = QSerializer(base64=True)
        return s.loads(self.b64_query)

    @query.setter
    def query(self, value):
        """
        Serialize an ORM query, Base-64 encode it and set it to
        the b64_query field
        """
        if not isinstance(value, Q):
            raise Exception('Must only be passed a Django (Q)uery object')
        s = QSerializer(base64=True)
        self.b64_query = s.dumps(value)

    def list_fields(self):
        if self.b64_query == '':
            return []
        else:
            s = QSerializer(base64=True)
            d = s.loads(self.b64_query, raw=True)
            return s.get_field_values_list(d)

    def initialize_form(self):
        """ Takes a "finalized" query and generate it's form data """
        model = get_model(*self.model.split('.'))
        initials = []
        for field_data in self.list_fields():
            initials.append(self._parse_query_dict(field_data, model))
        return initials

    def _parse_query_dict(self, query_data, model):
        """
        Take a list of query field dict and return data for form initialization
        """
        if query_data['field'] == '_OR':
            query_data['operator'] = 'iexact'
            return query_data

        parts = query_data['field'].split('__')
        if len(parts) < 2:
            field = parts[0]
        else:
            if parts[-1] in dict(Condition.OPERATORS).keys():
                field = '__'.join(parts[:-1])
                query_data['operator'] = parts[-1]
            else:
                field = query_data['field']
        query_data['field'] = field

        mfield = get_fields_from_path(model, query_data['field'])
        if not mfield:
            raise Exception('Field path "%s" could not be followed to a field'
                            ' in model %s', query_data['field'], model)
        else:
            mfield = mfield[-1]  # get the field object

        if query_data['value'] is None:
            query_data['operator'] = "isnull"
        elif query_data['value'] is True:
            query_data['operator'] = "istrue"
            query_data['value'] = None
        elif query_data['value'] is False:
            query_data['operator'] = "isfalse"
            query_data['value'] = None
        # can be range without Date
        # else:
        #     if isinstance(mfield, DateField):
        #         # this is a date/datetime field
        #         query_data['operator'] = "range"  # default
        #     else:
        #         query_data['operator'] = "iexact"  # default

        if isinstance(query_data.get('value'), list) and query_data['operator'] == 'range':
            dtfrom = dt.fromtimestamp(query_data.get('value_from', 0))
            dtto = dt.fromtimestamp(query_data.get('value_to', 0))
            query_data['value'] = ','.join([dtfrom.strftime('%Y-%m-%d'), dtto.strftime('%Y-%m-%d')])

        return query_data

    def get_fields_from_model(self):
        """
        Iterate over given <field> names (in "orm query" notation) and find
        the actual field given the initial <model>.

        If <field> is a tuple of the format ('field_name', 'Verbose name'),
        overwrite the field's verbose name with the given name for display
        purposes.
        """
        from django.contrib import admin
        from django.contrib.admin.util import get_fields_from_path

        model = get_model(*self.model.split('.'))
        try:
            model_admin = admin.site._registry[model]
        except KeyError:
            logger.debug('No ModelAdmin registered for %s', model)

        self._filter_fields = getattr(model_admin, 'advanced_filter_fields', ())

        model_fields = {}
        for field in self._filter_fields:
                if isinstance(field, tuple) and len(field) == 2:
                    field, verbose_name = field[0], field[1]
                else:
                    try:
                        model_field = get_fields_from_path(model, field)[-1]
                        verbose_name = model_field.verbose_name
                    except (FieldDoesNotExist, IndexError, TypeError) as e:
                        logger.warn("AdvancedFilterForm: skip invalid field - %s", e)
                        continue
                model_fields[field] = verbose_name
        # Iterate over model fields dict and return tuple for initial choices
        model_fields = [(fquery, fname.capitalize()) for fquery, fname in model_fields.items()]
        return tuple(sorted(model_fields, key=lambda f: f[1].lower())) + self.FIELD_CHOICES

    def get_model_choices(self):
        """ Get a list of models with advanced_filters support and return a tuple for model field choices. """
        from django.contrib import admin

        choices = ()
        for model, modeladmin in admin.site._registry.iteritems():
            if hasattr(modeladmin, 'has_advanced_filter'):
                choices += ((model._meta.app_label + '.' + model._meta.model_name.capitalize(), model._meta.verbose_name),)
        return choices

class Condition(models.Model):

    OPERATORS = (
        ("iexact", _("Equals")),
        ("icontains", _("Contains")),
        ("in", _("In")),
        ("gt", _("Greater than")),
        ("gte", _("Greater than or equal to")),
        ("lt", _("Less than")),
        ("lte", _("Less than or equal to")),
        ("istartswith", _("Starts with")),
        ("iendswith", _("Ends with")),
        ("range", _("Range")),
        ("isnull", _("Is NULL")),
        ("istrue", _("Is TRUE")),
        ("isfalse", _("Is FALSE")),
    )

    afilter = models.ForeignKey(AdvancedFilter, on_delete=models.DO_NOTHING, verbose_name=_('filter'))
    field = models.CharField(_('field'), max_length=255, null=False, blank=False)
    operator = models.CharField(_('operator'), max_length=20, choices=OPERATORS, default='iexact', null=False, blank=False)
    value = models.CharField(_('value'), max_length=255, null=True, blank=True)
    # value_to = models.CharField(_('value to'), max_length=255, null=True, blank=True)
    negate = models.BooleanField(_('negate'), blank=True)
    delete = models.BooleanField(_('delete'), blank=True)

    class Meta:
        managed = False
        verbose_name = _('Condition')
        verbose_name_plural = _('Conditions')

    def make_query(self, *args, **kwargs):
        """ Returns a Q object. """
        query = Q()  # initial is an empty query
        query_dict = self.build_query_dict()
        if self.negate:
            query = query & ~Q(**query_dict)
        else:
            query = query & Q(**query_dict)
        return query

    def build_query_dict(self):
        """ Create a query dict to be used in a Q object (or filter). """
        if self.operator == "istrue":
            return {self.field: True}
        elif self.operator == "isfalse":
            return {self.field: False}
        else:
            key = "{}__{}".format(self.field, self.operator)
            if self.operator == "isnull":
                return {key: True}
            else:
                return {key: self.value}
