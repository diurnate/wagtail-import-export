from django.db import models
from wagtail.admin.edit_handlers import (
    FieldPanel, 
    StreamFieldPanel, 
    PageChooserPanel, 
)
from wagtail.core import blocks
from wagtail.core.models import Page
from wagtail.core.fields import StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.images.edit_handlers import ImageChooserPanel
from wagtail.snippets.blocks import SnippetChooserBlock
from wagtail.snippets.edit_handlers import SnippetChooserPanel
from wagtail.snippets.models import register_snippet


@register_snippet
class TestSnippet(models.Model):
    """A snippet model for testing purposes."""
    text = models.CharField(max_length=255)
    image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    link = models.ForeignKey(
        'wagtailcore.Page',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    snippet = models.ForeignKey(
        'testapp.TestSnippet',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    body = StreamField([
        ('heading', blocks.CharBlock()),
        ('content', blocks.RichTextBlock()),
        ('link', blocks.PageChooserBlock()),
        ('image', ImageChooserBlock()),
    ], null=True, blank=True)

    panels = [
        FieldPanel('text'),
        ImageChooserPanel('image'),
        PageChooserPanel('link'),
        SnippetChooserPanel('snippet'),
        StreamFieldPanel('body'),
    ]

    def __str__(self):
        return self.text


class TestPage(Page):
    image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    link = models.ForeignKey(
        'wagtailcore.Page',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    snippet = models.ForeignKey(
        'testapp.TestSnippet',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    body = StreamField([
        ('heading', blocks.CharBlock()),
        ('content', blocks.RichTextBlock()),
        ('link', blocks.PageChooserBlock()),
        ('image', ImageChooserBlock()),
        ('snippet', SnippetChooserBlock(TestSnippet)),
    ], null=True, blank=True)

    content_panels = Page.content_panels + [
        ImageChooserPanel('image'),
        PageChooserPanel('link'),
        SnippetChooserPanel('snippet'),
        StreamFieldPanel('body'),
    ]

    def __str__(self):
        return "%d %s" % (self.id, self.title)
