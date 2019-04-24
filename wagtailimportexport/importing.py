import io
import os
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.files import File
from django.db import models, transaction
from modelcluster.models import get_all_child_relations
from wagtail.images import get_image_model
from wagtailimportexport.compat import Page


@transaction.atomic()
def import_pages(content_data, parent_page):
    """
    Take a JSON export of part of a source site's page tree
    and create those pages under the parent page
    """
    pages_by_original_path = {}
    pages_by_original_id = {}

    # First create the base Page records; these contain no foreign keys, so this allows us to
    # build a complete mapping from old IDs to new IDs before we go on to importing the
    # specific page models, which may require us to rewrite page IDs within foreign keys / rich
    # text / streamfields.
    page_content_type = ContentType.objects.get_for_model(Page)
    for (i, page_record) in enumerate(content_data['pages']):
        # build a base Page instance from the exported content (so that we pick up its title and other
        # core attributes)
        page = Page.from_serializable_data(page_record['content'])
        original_path = page.path
        original_id = page.id

        # clear id and treebeard-related fields so that they get reassigned when we save via add_child
        page.id = None
        page.path = None
        page.depth = None
        page.numchild = 0
        page.url_path = None
        page.content_type = page_content_type
        if i == 0:
            parent_page.add_child(instance=page)
        else:
            # Child pages are created in the same sibling path order as the
            # source tree because the export is ordered by path
            parent_path = original_path[:-(Page.steplen)]
            pages_by_original_path[parent_path].add_child(instance=page)

        pages_by_original_path[original_path] = page
        pages_by_original_id[original_id] = page

    for (i, page_record) in enumerate(content_data['pages']):
        # Get the page model of the source page by app_label and model name
        # The content type ID of the source page is not in general the same
        # between the source and destination sites but the page model needs
        # to exist on both.
        # Raises LookupError exception if there is no matching model
        model = apps.get_model(page_record['app_label'], page_record['model'])

        specific_page = model.from_serializable_data(page_record['content'], check_fks=False, strict_fks=False)
        base_page = pages_by_original_id[specific_page.id]
        specific_page.page_ptr = base_page
        specific_page.__dict__.update(base_page.__dict__)
        specific_page.content_type = ContentType.objects.get_for_model(model)
        update_page_references(specific_page, pages_by_original_id)
        specific_page.save()

    return len(content_data['pages'])


def update_page_references(model, pages_by_original_id):
    for field in model._meta.get_fields():
        if isinstance(field, models.ForeignKey) and issubclass(field.related_model, Page):
            linked_page_id = getattr(model, field.attname)
            try:
                # see if the linked page is one of the ones we're importing
                linked_page = pages_by_original_id[linked_page_id]
            except KeyError:
                # any references to pages outside of the import should be left unchanged
                continue

            # update fk to the linked page's new ID
            setattr(model, field.attname, linked_page.id)

    # update references within inline child models, including the ParentalKey pointing back
    # to the page
    for rel in get_all_child_relations(model):
        for child in getattr(model, rel.get_accessor_name()).all():
            # reset the child model's PK so that it will be inserted as a new record
            # rather than updating an existing one
            child.pk = None
            # update page references on the child model, including the ParentalKey
            update_page_references(child, pages_by_original_id)


@transaction.atomic()
def import_snippets(content_data):
    """Import the snippets given in the content_data (formatted as in content.json 
    produced by the exporting module).
    Existing snippets with the same pk are overwritten with the new snippet record.
    (This is a reasonable assumption for imports to environments that are essentially 
    copies, and it greatly simplifies by not requiring that we rewrite any foreign keys
    to snippets in the imported page and snippet data. However, it could result in some
    weird snippet replacements in the imported enviroment. A future upgrade would be
    to look at the snippet model, and if there is a unique field other than id, use that
    to match the new snippet with the one it replaces. But this requires managing and 
    updating snippet foreign keys everywhere they occur (including recursively walking
    StreamFields). That's a lot more work, -- so, this simpler version for now.)
    """
    for model_key in content_data['snippets']:
        Snippet = apps.get_model(model_key)
        for snippet_data in content_data['snippets'][model_key]:
            snippet = Snippet.objects.filter(id=snippet_data['id']).first()
            if snippet is not None:
                snippet.__dict__.update(**{snippet_data})
            else:
                snippet = Snippet(**{snippet_data})
            snippet.save()


@transaction.atomic()
def import_images(content_data, path):
    """Import the images given in the content_data (formatted as in content.json 
    produced by the exporting module) and under the given base path (the folder 
    in which content.json is saved, with images under that location).
    Existing images with the same pk are overwritten with the new image record & file. 
    (This is a reasonable assumption for imports to environments that are essentially 
    copies, and it greatly simplifies by not requiring that we rewrite any foreign keys
    to images in the imported page and snippet data. However, it could result in some
    weird image replacements in the imported enviroment. A future upgrade would look 
    at image file name and overwrite images with the same file name, and update the 
    foreign keys to refer to the correct image. But that's a lot more work....)
    """
    Image = get_image_model()
    for image_data in content_data['images']:
        image = Image.objects.filter(id=image_data['id']).first()
        if image is not None:
            image.__dict__.update(
                **{
                    key: value
                    for key, value in image_data.items()
                    if key != 'file' and value is not None
                })
        else:
            image = Image(
                **{
                    key: value
                    for key, value in image_data.items()
                    if key != 'file' and value is not None
                })
        image_filename = os.path.join(path, image_data['file']['name'])
        image.file = File(
            name=os.path.basename(image_filename),
            file=open(image_filename, 'rb'),
        )
        image.save()


