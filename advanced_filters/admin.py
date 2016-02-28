import logging

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.utils.translation import ugettext_lazy as _
from django.contrib.admin.util import unquote
from django.shortcuts import resolve_url

from .forms import AdvancedFilterForm
from .models import AdvancedFilter, Condition


logger = logging.getLogger('advanced_filters.admin')


class AdvancedListFilters(admin.SimpleListFilter):
    """Allow filtering by stored advanced filters (selection by title)"""
    title = _('Advanced filters')

    parameter_name = '_afilter'

    def __init__(self, request, params, model, model_admin):
        super(AdvancedListFilters, self).__init__(request, params, model, model_admin)
        self.model_admin = model_admin

    def lookups(self, request, model_admin):
        if not model_admin:
            raise Exception('Cannot use AdvancedListFilters without a model_admin')
        filters = [(t[0], t[1]) for t in model_admin.advanced_filter_custom]

        model_name = "%s.%s" % (model_admin.model._meta.app_label,
                                model_admin.model._meta.object_name)
        filters += AdvancedFilter.objects.filter_by_user(request.user).filter(
            model=model_name).values_list('id', 'title')

        return filters

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            filters = {t[0]: t[2] for t in self.model_admin.advanced_filter_custom}
            if value in filters:
                query = filters[value]
                return queryset.filter(query).distinct()
            else:
                advfilter = AdvancedFilter.objects.filter(id=value).first()
                if not advfilter:
                    logger.error("AdvancedListFilters.queryset: Invalid filter id")
                    return queryset
                query = advfilter.query
                logger.debug(query.__dict__)
                return queryset.filter(query).distinct()
        return queryset


class BaseAdminAdvancedFiltersMixin(object):
    """ Base AdvancedFilters mixin. Only adds AdvancedListFilters to list_filter """

    def __init__(self, *args, **kwargs):
        super(BaseAdminAdvancedFiltersMixin, self).__init__(*args, **kwargs)
        self.has_advanced_filter = True
        # add list filters to filters
        self.list_filter = (AdvancedListFilters,) + self.list_filter


class AdminAdvancedFiltersMixin(BaseAdminAdvancedFiltersMixin):
    """ Generic AdvancedFilters mixin """
    change_list_template = "admin/advanced_filters.html"
    advanced_filter_form = AdvancedFilterForm

    def save_advanced_filter(self, request, form):
        if form.is_valid():
            afilter = form.save(commit=False)
            afilter.created_by = request.user
            afilter.query = form.generate_query()
            afilter.save()
            afilter.users.add(request.user)
            messages.add_message(
                request, messages.SUCCESS,
                _('Advanced filter added successfully.')
            )
            if '_save_goto' in request.REQUEST:
                url = "{path}{qparams}".format(
                    path=request.path, qparams="?_afilter={id}".format(
                        id=afilter.id))
                return HttpResponseRedirect(url)
        elif request.method == "POST":
            logger.info('Failed saving advanced filter, params: %s', form.data)

    def adv_filters_handle(self, request, extra_context={}):
        data = request.POST if request.REQUEST.get(
            'action') == 'advanced_filters' else None
        adv_filters_form = self.advanced_filter_form(
            data=data, model_admin=self, extra_form=True)
        extra_context.update({
            'advanced_filters': adv_filters_form,
            'current_afilter': request.GET.get('_afilter'),
            'app_label': self.opts.app_label,
        })
        return self.save_advanced_filter(request, adv_filters_form)

    def changelist_view(self, request, extra_context=None):
        """Add advanced_filters form to changelist context"""
        if extra_context is None:
            extra_context = {}
        response = self.adv_filters_handle(request,
                                           extra_context=extra_context)
        if response:
            return response
        return super(AdminAdvancedFiltersMixin, self
                     ).changelist_view(request, extra_context=extra_context)


# ============================================================================
# ============================================================================
# ============================================================================
# ============================================================================
# ============================================================================


from .forms import CustomInlineFormset, ConditionModelForm, SimpleAdvancedFilterForm
from django.db.models import Q


class ConditionInline(admin.TabularInline):
    """
    Custom inline admin that support initial data
    """
    form = ConditionModelForm
    formset = CustomInlineFormset

    model = Condition
    extra = 1
    # max_num = 10
    can_delete = True

    def get_queryset(self, request):
        """return empty queryset"""
        return Condition.objects.none()

    def get_extra(self, request, obj=None, **kwargs):
        return self.extra if obj.b64_query == '' else 0


class AdvancedFilterAdmin(admin.ModelAdmin):
    model = AdvancedFilter
    form = SimpleAdvancedFilterForm

    list_display = ('title', 'model', 'created_by',)
    list_link_display = ('title',)

    inlines = (ConditionInline,)

    # def has_add_permission(self, obj=None):
    #     return False

    # def change_view(self, request, object_id, form_url='', extra_context=None):
    #     orig_response = super(AdvancedFilterAdmin, self).change_view(
    #         request, object_id, form_url, extra_context)
    #     if '_save_goto' in request.POST:
    #         obj = self.get_object(request, unquote(object_id))
    #         if obj:
    #             app, model = obj.model.split('.')
    #             path = resolve_url('admin:%s_%s_changelist' % (
    #                 app, model.lower()))
    #             url = "{path}{qparams}".format(
    #                 path=path, qparams="?_afilter={id}".format(id=object_id))
    #             return HttpResponseRedirect(url)
    #     return orig_response

    def _create_formsets(self, request, obj, change):
        """overide to provide initial data for inline formset"""
        formsets = []
        inline_instances = []
        prefixes = {}
        get_formsets_args = [request]
        if change:
            get_formsets_args.append(obj)
        for FormSet, inline in self.get_formsets_with_inlines(*get_formsets_args):
            prefix = FormSet.get_default_prefix()
            prefixes[prefix] = prefixes.get(prefix, 0) + 1
            if prefixes[prefix] != 1 or not prefix:
                prefix = "%s-%s" % (prefix, prefixes[prefix])
            formset_params = {
                'instance': obj,
                'prefix': prefix,
                'queryset': inline.get_queryset(request),
            }
            if request.method == 'POST':
                formset_params.update({
                    'data': request.POST,
                    'files': request.FILES,
                    'save_as_new': '_saveasnew' in request.POST
                })
            # Set field_choices and inital data
            formset_params['filter_fields'] = obj.get_fields_from_model()
            formset_params['initial'] = obj.initialize_form()

            formsets.append(FormSet(**formset_params))
            inline_instances.append(inline)
        return formsets, inline_instances

    def get_formsets_with_inlines(self, request, obj=None):
        """ Don't show inlines on creation. Only show on edit. """
        for inline in self.get_inline_instances(request, obj):
            if obj is not None:
                yield inline.get_formset(request, obj), inline

    def get_readonly_fields(self, request, obj=None):
        """ Can't change model. """
        if obj is None:
            return ('created_by',)
        else:
            return ('created_by', 'model')

    def save_model(self, request, new_object, *args, **kwargs):
        """ Only save on new filters, otherwise save on save_formset(). """
        if new_object.pk is None:
            new_object.created_by = request.user
        new_object.save()
        # else:
        #     pass

    def save_formset(self, request, form, formset, change):
        """ Get Condition instances, generate query and save AdvancedFilter. """
        conditions = formset.save(commit=False)
        form.instance.query = self.generate_query(conditions)
        form.instance.save()

    def generate_query(self, conditions):
        """ Reduces multiple queries into a single usable query. """
        query = Q()
        ORed = []
        for condition in conditions:
            if not condition.delete:
                if condition.field == "_OR":
                    ORed.append(query)
                    query = Q()
                else:
                    query = query & condition.make_query()
        if ORed:
            if query:  # add last query for OR if any
                ORed.append(query)
            query = reduce(operator.or_, ORed)
        return query

admin.site.register(AdvancedFilter, AdvancedFilterAdmin)
