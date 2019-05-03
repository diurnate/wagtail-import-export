from django.test import TestCase
from wagtail.images import get_image_model
from wagtail_factories import ImageFactory

from wagtailimportexport.compat import Page
from wagtailimportexport import importing  # read this aloud

from testapp.models import TestSnippet

class TestImportingPages(TestCase):
    def setUp(self):
        self.clean_db()

    def clean_db(self):
        # Tabula rasa
        root_page = Page.objects.first()
        root_page.get_descendants().delete()
        Image = get_image_model()
        Image.objects.all().delete()
        TestSnippet.objects.all().delete()

    def test_import_pages_by_url_fails_if_no_matching_root(self):
        content = {
            'pages': [{
                'content': {'path': '0022', 'title': 'Home', 'slug': 'home'}
            }]
        }
        result = importing.import_pages_by_url(content)
        assert 'failures' in result

    def test_import_pages_by_url_matches_root_and_updates(self):
        self.clean_db()
        root_page = Page.objects.first()
        new_page = Page(title="Not Home", slug="not-home", path="00010001")
        root_page.add_child(instance=new_page)
        content = {
            'pages': [{
                'content': {'path': '00010001', 'title': 'Home', 'slug': 'home', 'pk': 3},
                "model": "homepage",
                "app_label": "home"
            }]
        }
        page = Page.objects.filter(path="00010001")
        assert page.count() == 1
        assert page[0].title == 'Not Home'
        result = importing.import_pages_by_url(content)
        assert page[0].title == 'Home'

    def test_import_pages_by_url_matches_last_four_of_path_and_updates(self):
        self.clean_db()
        root_page = Page.objects.first()
        new_page = Page(title="Not Home", slug="not-home", path="0001")
        root_page.add_child(instance=new_page)
        content = {
            'pages': [{
                'content': {'path': '00010001', 'title': 'Home', 'slug': 'home', 'pk': 3},
                "model": "homepage",
                "app_label": "home"
            }]
        }
        page = Page.objects.filter(path="00010001")
        assert page.count() == 1
        assert page[0].title == 'Not Home'
        result = importing.import_pages_by_url(content)
        assert page[0].title == 'Home'

    def test_update_page_ids_maps_new_pk_to_old_and_preserves_new_if_no_match(self):
        self.clean_db()
        root_page = Page.objects.first()
        new_page = Page(title="Not Home", slug="not-home", path="00010001")
        root_page.add_child(instance=new_page)
        page = Page.objects.filter(path='00010001')
        existing_id = page[0].pk
        next_id = existing_id + 101
        other_id = next_id + 11
        content = {
            'pages': [
                {
                    'content': {'path': '00010001', 'pk': next_id},
                },
                {
                    'content': {'path': '00010002', 'pk': other_id}
                }
            ]
        }
        result, id_map = importing.update_page_ids(content)
        assert existing_id == result['pages'][0]['content']['pk']
        assert existing_id == id_map[next_id]
        assert other_id == id_map[other_id]
        assert other_id == result['pages'][1]['content']['pk']

