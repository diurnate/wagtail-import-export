import io, json, logging, os, re, sys
from lxml import etree
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.files import File
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from modelcluster.models import get_all_child_relations
from wagtail.core.blocks import (
    PageChooserBlock, RichTextBlock, StructBlock, StreamBlock, StreamValue)
from wagtail.core.rich_text import RichText

from wagtail.core.fields import RichTextField, StreamField
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
        # build a base Page instance from the exported content (so we pick up its title and other
        # core attributes)
        page = Page.from_serializable_data(page_record['content'])
        original_path = page.path
        original_id = page.id

        # clear id and treebeard-related fields so they get reassigned when we save via add_child
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

        specific_page = model.from_serializable_data(
            page_record['content'], check_fks=False, strict_fks=False)
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


def import_pages_by_url(content_data):
    """
    Import the pages given in the content_data (content.json from exporting).    
    * Imported page replaces existing page that has the same Page.path (unique).
      (ASSUMPTION: A page at the same unique location (path) is the same page.
      Also, either the first page in the content_data, or its parent, must exist.)
    * In order to preserve foreign keys to pages, 
      1. import pages are given the same id as the existing page they replace.
      2. foreign key references to import pages in the content_data are rewritten 
         to point to the new id.
      Snippets and Pages can include foreign keys to pages, either in foreign key 
      fields directly, or buried inside StreamFields. Thus recursion ensues.
    """
    # Ensure that the first page, or its parent, exists.
    first_page_path = content_data['pages'][0]['content']['path']
    first_page = Page.objects.filter(path=first_page_path).first()
    if first_page is None:
        first_page_parent_path = first_page_path[:-4]
        first_page_parent = Page.objects.filter(path=first_page_parent_path).first()
        if first_page_parent is None:
            first_page_content = content_data['pages'][0]['content']
            return {
                'failures': [
                    'Pages could not be imported because the path to the first page '
                    + ('(title=%(title)r, url_slug=%(slug)r)' % first_page_content)
                    + 'could not be located. Please import to a location that exists.'
                ]
            }

    # update page ids and foreign keys
    content_data, page_id_map = update_page_ids(content_data)
    content_data, results = update_page_foreign_keys(content_data, page_id_map)

    # actually import the pages
    import_results = import_pages_content(content_data)
    for key in import_results:
        results[key] = (results.get(key) or []) + import_results[key]

    return results


def update_page_ids(content_data):
    """
    Match pages in content_data to existing pages and update the page ids in content_data.
    * Use the Page.path (unique) to match pages.
    * DESTRUCTIVE: Mutates page ids in-place.
    * Return a mapping from old (exported) Page ids to new Page ids.
    """
    existing_page_id_map = {page.path: page.id for page in Page.objects.all()}
    page_id_map = {}
    for page_record in content_data['pages']:
        page_content = page_record['content']
        if page_content['path'] in existing_page_id_map:
            existing_page_id = existing_page_id_map[page_content['path']]
            page_id_map[page_content['pk']] = existing_page_id_map[page_content['path']]
            page_content['pk'] = existing_page_id
        else:
            page_id_map[page_content['pk']] = page_content['pk']
    return content_data, page_id_map


def update_page_foreign_keys(content_data, page_id_map):
    """
    Update foreign key references to pages in (import) content_data.
    * DESTRUCTIVE: Mutates foreign keys to page ids in-place.
    * Returns a list of warnings + errors encountered during processing
    """
    fields_map = {}  # cache model fields as we come to them
    results = {'warnings': [], 'errors': [], 'failures': []}
    for index, page_record in enumerate(content_data['pages']):
        model_key = '.'.join([page_record['app_label'], page_record['model']])
        PageModel = apps.get_model(model_key)
        if model_key not in fields_map:
            fields_map[model_key] = {
                field.name: field
                for field in PageModel._meta.fields
            }
        model_fields = fields_map[model_key]
        page_content = page_record['content']
        page_id = page_content['pk']
        logging.debug("# update_page_foreign_keys: %r" % page_id)
        for field_name, value in page_content.items():
            if field_name in model_fields:
                field = model_fields[field_name]
                if isinstance(field.__dict__.get('related_model'), Page):
                    page_content[field_name], result = update_page_fks_in_field(
                        value, page_id, field_name, page_id_map)
                    for key in result:
                        results[key] += result[key]

                elif isinstance(field, RichTextField):
                    # this is a little harder
                    page_content[field_name], result = update_page_fks_in_rich_text(
                        value, page_id, field_name, page_id_map)
                    for key in result:
                        results[key] += result[key]

                elif isinstance(field, StreamField):
                    # this is much harder...
                    page_fields = fields_map[model_key]
                    page_content[field_name], result = update_page_fks_in_streamfield(
                        value, page_id, field_name, page_id_map, page_fields)
                    for key in result:
                        results[key] += result[key]

                # else ignore -- no page foreign keys in the field

        # explicit is better than implicit
        page_record['content'] = page_content
        content_data[index] = page_record

    return content_data, results


def update_page_fks_in_field(value, page_id, field_name, page_id_map):
    """Update the field value (a page id) according to the page_id_map, if found."""
    logging.debug("## update_page_fks_in_field: %r %r" % (page_id, field_name))
    if value in page_id_map:
        value = page_id_map[value]
    else:
        if field.null:
            results['warnings'].append(
                "%s(id=%s).%s=%d not found, setting null" %
                (model_key, page_id, field_name, value))
            value = None
        else:
            results['errors'].append(
                "%s(id=%s).%s=%d not found, CANNOT SET NULL" %
                (model_key, page_id, field_name, value))
    return value, result


def update_page_fks_in_rich_text(value, page_id, field_name, page_id_map):
    """Find page ids in RichText value and update according to the page_id_map"""
    logging.debug("## update_page_fks_in_rich_text: %r %r" % (page_id, field_name))
    result = {'warnings': [], 'errors': [], 'failures': []}
    element = etree.fromstring("<richtext>%s</richtext>" % value)
    for a in element.xpath('//a[@id and @linktype="page"]'):
        link_page_id = int(a.get('id'))
        if link_page_id in page_id_map:
            a.set('id', str(page_id_map[link_page_id]))
        else:
            result['warnings'].append(
                "Page(id=%d) not found in Page(id=%d).%s in RichText" %
                (link_page_id, page_id, field_name))
    value = richtext_element_to_string(element)
    return value, result


def richtext_element_to_string(element):
    """Canonicalization ensures that element tags are not minimized."""
    bio = io.BytesIO()
    etree.ElementTree(element=element).write_c14n(bio)
    s = re.sub(r'</?richtext>', r'', bio.getvalue().decode('utf-8'))
    return s


def update_page_fks_in_streamfield(value, page_id, field_name, page_id_map,
                                   page_fields):
    """Find page ids in StreamField value and update according to the page_id_map"""
    logging.debug("## update_page_fks_in_streamfield: %r %r" % (page_id, field_name))
    result = {'warnings': [], 'errors': [], 'failures': []}
    stream_block = page_fields[field_name].stream_block
    stream_data = json.loads(value)
    stream_data, res = update_page_fks_in_stream_data(
        stream_data, stream_block, page_id, field_name, page_id_map)
    for key in res:
        result[key] += res[key]
    value = json.dumps(stream_data, cls=DjangoJSONEncoder)
    return value, result


def update_page_fks_in_stream_data(stream_data, stream_block, page_id,
                                   field_name, page_id_map):
    """Iterate stream_data updating page foreign keys according to the page_id_map"""
    logging.debug("## update_page_fks_in_stream_data: %r %r %r" % (page_id, field_name, stream_block))
    logging.debug('### %r %r' % (type(stream_data), stream_data))
    result = {'warnings': [], 'errors': [], 'failures': []}
    for i, block_data in enumerate(stream_data):
        logging.debug('#### %r %r' % (type(block_data), block_data))
        if block_data['type'] in stream_block.child_blocks.keys():
            child_block = stream_block.child_blocks[block_data['type']]
            block_data, res = update_page_fks_in_block(
                block_data, child_block, page_id, field_name, page_id_map)
            stream_data[i] = block_data
            for key in res:
                result[key] += res[key]
        else:
            result['warnings'].append(
                "Block type=%r not found in Page(id=%d).%s in StreamBlock=%r" %
                (block_data['type'], page_id, field_name, stream_block))

    return stream_data, result


def update_page_fks_in_block(block_data, block, page_id, field_name,
                             page_id_map):
    """Update the page foreign keys in the StreamField block"""
    logging.debug("## update_page_fks_in_block: %r %r %r" % (page_id, field_name, block))
    result = {'warnings': [], 'errors': [], 'failures': []}
    if isinstance(block, PageChooserBlock):
        link_page_id = block_data['value']
        if link_page_id in page_id_map:
            block_data['value'] = page_id_map[link_page_id]
        else:
            result['warnings'].append(
                "Page(id=%d) not found in Page(id=%d).%s, in StreamBlock=%r" %
                (link_page_id, page_id, field_name, block))

    elif isinstance(block, RichTextBlock):
        block_data['value'], result = update_page_fks_in_rich_text(
            block_data['value'], page_id, field_name, page_id_map)

    elif isinstance(block, StructBlock):
        block_data, result = update_page_fks_in_struct_block(
            block_data, block, page_id, field_name, page_id_map)

    elif isinstance(block, StreamBlock):
        block_data['value'], result = update_page_fks_in_stream_data(
            block_data['value'], block, page_id, field_name, page_id_map)

    # else ignore -- no page foreign keys in the stream_data

    return block_data, result


def update_page_fks_in_struct_block(block_data, block, page_id, field_name,
                                    page_id_map):
    logging.debug("## update_page_fks_in_struct_block: %r %r %r" % (page_id, field_name, block))
    result = {'warnings': [], 'errors': [], 'failures': []}
    for name in block_data['value']:
        if name in block.child_blocks:
            block_data['value'][name], res = update_page_fks_in_block(
                page_id, field_name, block_data['value'][name],
                block.child_blocks[name], page_id_map)
            for key in res:
                result[key] += res[key]
        else:
            result['warnings'].append(
                "Block type=%r not found in Page(id=%d).%s in StreamBlock=%r" %
                (block_data['type'], page_id, field_name, block))

    return block_data, result


def import_pages_content(content_data):
    """
    Import the pages_data in the content_data as given. 
    ASSUMPTIONS: 
    * All foreign keys are as they should be.
    * All imported pages that have the same id as an existing page has the same Model
    * Imported pages that match an existing page retain the Page class of the existing page.
    Returns results messages
    """
    result = {'warnings': [], 'errors': [], 'failures': [], 'pages': []}
    existing_pages = {page.id: page.specific for page in Page.objects.all()}
    for page_record in content_data['pages']:
        page_content = page_record['content']
        page_id = page_content['pk']
        Model = apps.get_model(page_record['app_label'], page_record['model'])
        page_content['content_type'] = ContentType.objects.get_for_model(Model)
        page_content['live_revision'] = None
        if page_id in existing_pages:
            specific_page = existing_pages[page_id]
            if specific_page.__class__ != Model:
                result['warnings'].append(
                    "Existing Page(id=%d) class %s != import class %s" %
                    (specific_page.id, specific_page.__class__.__name__,
                     Model.__name__))
        else:
            specific_page = Model()
        logging.debug(page_content)
        try:
            specific_page = update_page_fields(specific_page, page_content)
            specific_page.save()
            result['pages'].append("(%d) %s" % (specific_page.id, specific_page.title))
        except:
            result['failures'].append(
                "%s(id=%d) could not be saved: %s" % (Model.__name__, page_id,
                                                      sys.exc_info()[1]))
            logging.debug(result)
            raise
    return result


def update_page_fields(page, page_content):
    """Care must be taken when updating page fields:
    * Only assign fields that exist in the page Model
    * StreamFields must have stream_data applied to their .stream_data property
    """
    fields_map = {
        field.name.split('.')[-1]: field
        for field in page.__class__._meta.fields
    }
    logging.debug("# fields_map: %r" % fields_map)
    for name, value in page_content.items():
        if name in fields_map:
            field = fields_map[name]
            logging.debug('## %r %r %r' % (name, type(field), value))
            if isinstance(field, StreamField):
                stream_block = page.__dict__[name].stream_block
                stream_data = json.loads(page_content[name])
                stream_import_data = stream_data_to_stream_import_data(stream_data, stream_block)
                # page.__dict__[name] = StreamValue(stream_block, [])
                page.__dict__[name].stream_data = stream_import_data
            else:
                page.__dict__[name] = page_content[name]
    return page


def stream_data_to_stream_import_data(stream_data, stream_block):
    """convert streamfield data to stream_data format: only keep 'type' and 'value' keys"""
    stream_import_data = []
    for block_data in stream_data:
        logging.debug(block_data)
        block_type = block_data['type']
        block_value = block_data['value']
        if block_type in stream_block.child_blocks:
            child_block = stream_block.child_blocks[block_type]
            if isinstance(stream_block.child_blocks[block_type], StreamBlock):
                block_value = stream_data_to_stream_import_data(block_value, child_block)
            stream_import_data.append({'type': block_type, 'value': block_value})
    return stream_import_data



def import_snippets(content_data):
    """
    Import the snippets given in the content_data (content.json from exporting).
    Existing snippets with the same pk are overwritten with the new snippet record.
    (This is a reasonable assumption for imports to environments that are essentially 
    copies, and it greatly simplifies by not requiring that we rewrite any foreign keys
    to snippets in the imported page and snippet data. However, it could result in some
    weird snippet replacements in the imported enviroment. A future upgrade would look 
    at the snippet model, and if there is a unique field other than id, use that to 
    match the new snippet with the one it replaces. But this requires managing and 
    updating snippet foreign keys everywhere they occur (including recursively walking
    StreamFields). That's a lot more work, -- so, this simpler version for now.)
    """
    imported_snippets = []
    for model_key in content_data['snippets']:
        Snippet = apps.get_model(model_key)
        for snippet_data in content_data['snippets'][model_key]:
            snippet = Snippet.objects.filter(id=snippet_data['id']).first()
            if snippet is not None:
                snippet.__dict__.update(**snippet_data)
            else:
                snippet = Snippet(**snippet_data)
            snippet.save()
            imported_snippets.append(str(snippet))
    return imported_snippets


def import_images(content_data, path):
    """
    Import the images given in the content_data (content.json from exporting, 
    images under the given path, such as when content.zip is unzipped).
    * Existing images with the same pk are overwritten with the new image record & file. 
      (This is a reasonable assumption for imports to environments that are essentially 
      copies, and it greatly simplifies by not requiring that we rewrite any foreign keys
      to images in the imported page and snippet data. However, it could result in some
      weird image replacements in the imported enviroment. A future upgrade would look 
      at image file name and overwrite images with the same file name, and update the 
      foreign keys to refer to the correct image. But that's a lot more work....)
    """
    Image = get_image_model()
    imported_images = []
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
        if image.file is not None:
            image.file.storage.delete(image.file.name)
        image.file = File(
            name=os.path.basename(image_filename),
            file=open(image_filename, 'rb'),
        )
        image.save()
        imported_images.append(str(image))
    return imported_images
