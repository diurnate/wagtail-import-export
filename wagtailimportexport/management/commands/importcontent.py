import json, os, logging, tempfile, zipfile
from django.core.management.base import BaseCommand
from wagtailimportexport.importing import (
    import_pages_by_url,
    import_snippets,
    import_images,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Import Wagtail content (pages, snippets, images) from a content.zip file'

    def add_arguments(self, parser):
        parser.add_argument(
            'filename',
            type=str,
            help='the filename for the exported content',
        )
        parser.add_argument(
            '-i',
            '--images',
            action="store_true",
            help='import images',
        )
        parser.add_argument(
            '-s',
            '--snippets',
            action="store_true",
            help='import snippets',
        )
        parser.add_argument(
            '-p',
            '--pages',
            action="store_true",
            help='import pages',
        )

    def handle(self, *args, **options):
        logger.info(options)
        with tempfile.TemporaryDirectory() as tempdir:
            with zipfile.ZipFile(os.path.abspath(options['filename'])) as zf:
                zf.extractall(path=tempdir)
            content_data_filename = os.path.join(tempdir, 'content.json')
            with open(content_data_filename, 'rb') as f:
                content_data = json.load(f)
            results = {
                'pages': [],
                'snippets': [],
                'images': [],
                'failures': [],
                'errors': [],
                'warnings': [],
            }
            if options['pages'] == True:
                results = import_pages_by_url(content_data)
                logger.info("Imported %d Page(s): %s" % (len(
                    results['pages']), ', '.join(results['pages'])))
            if options['snippets'] == True:
                results['snippets'] = import_snippets(content_data)
                logger.info("Imported %d Snippet(s): %s" % (len(
                    results['snippets']), ', '.join(results['snippets'])))
            if options['images'] == True:
                results['images'] = import_images(content_data, tempdir)
                logger.info("Imported %d Image(s): %s" % (len(
                    results['images']), ', '.join(results['images'])))
            if len(results['failures']) > 0:
                logger.critical('. '.join(results['failures']))
            if len(results['errors']) > 0:
                logger.error('. '.join(results['errors']))
            if len(results['warnings']) > 0:
                logger.warning('. '.join(results['warnings']))
