from django import forms
from django.utils.translation import ugettext as _

from wagtail.admin.widgets import AdminPageChooser
from wagtail.core.models import Page


class ImportForm(forms.Form):
    source_page_id = forms.IntegerField()
    source_site_base_url = forms.URLField()
    parent_page = forms.ModelChoiceField(
        queryset=Page.objects.all(),
        widget=AdminPageChooser(can_choose_root=True, user_perms='copy_to'),
        label=_("Destination parent page"),
        help_text=_("Imported pages will be created as children of this page.")
    )