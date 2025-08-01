from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import OuterRef, Subquery
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.fields import RichTextField
from wagtail.models import Orderable, Page, PagePermissionTester
from wagtail.search import index

from cms.articles.models import ArticleSeriesPage, StatisticalArticlePage
from cms.bundles.mixins import BundledPageMixin
from cms.core.fields import StreamField
from cms.core.models import BasePage
from cms.core.query import order_by_pk_position
from cms.core.utils import get_formatted_pages_list
from cms.datasets.blocks import DatasetStoryBlock
from cms.datasets.utils import format_datasets_as_document_list
from cms.methodology.models import MethodologyPage
from cms.taxonomy.mixins import ExclusiveTaxonomyMixin
from cms.topics.blocks import ExploreMoreStoryBlock, TopicHeadlineFigureBlock
from cms.topics.forms import TopicPageAdminForm
from cms.topics.viewsets import (
    FeaturedSeriesPageChooserWidget,
    HighlightedArticlePageChooserWidget,
    HighlightedMethodologyPageChooserWidget,
)

if TYPE_CHECKING:
    from django.http import HttpRequest
    from wagtail.admin.panels import Panel

    from cms.users.models import User

MAX_ITEMS_PER_SECTION = 3


class TopicPageRelatedArticle(Orderable):
    parent = ParentalKey("TopicPage", on_delete=models.CASCADE, related_name="related_articles")
    page = models.ForeignKey[Page](
        "wagtailcore.Page",
        on_delete=models.CASCADE,
        related_name="+",
    )

    panels: ClassVar[list[FieldPanel]] = [
        FieldPanel(
            "page", widget=HighlightedArticlePageChooserWidget(linked_fields={"topic_page_id": "#id_topic_page_id"})
        )
    ]


class TopicPageRelatedMethodology(Orderable):
    parent = ParentalKey("TopicPage", on_delete=models.CASCADE, related_name="related_methodologies")
    page = models.ForeignKey[Page](
        "wagtailcore.Page",
        on_delete=models.CASCADE,
        related_name="+",
    )

    panels: ClassVar[list[FieldPanel]] = [
        FieldPanel(
            "page", widget=HighlightedMethodologyPageChooserWidget(linked_fields={"topic_page_id": "#id_topic_page_id"})
        )
    ]


class TopicPagePermissionTester(PagePermissionTester):
    def can_add_subpage(self) -> bool:
        """Overrides the core can_add_subpage to consider max_count_per_parent.

        TODO: remove when https://github.com/wagtail/wagtail/issues/13286 is fixed
        """
        if not self.user.is_active:
            return False
        if (specific_class := self.page.specific_class) is None:
            return False
        creatable_subpage_models = [
            page_model
            for page_model in specific_class.creatable_subpage_models()
            if page_model.can_create_at(self.page)
        ]
        if specific_class is None or not creatable_subpage_models:
            return False
        return self.user.is_superuser or ("add" in self.permissions)


class TopicPage(BundledPageMixin, ExclusiveTaxonomyMixin, BasePage):  # type: ignore[django-manager-missing]
    """The Topic page model."""

    base_form_class = TopicPageAdminForm
    template = "templates/pages/topic_page.html"
    parent_page_types: ClassVar[list[str]] = ["home.HomePage"]
    subpage_types: ClassVar[list[str]] = ["articles.ArticlesIndexPage", "methodology.MethodologyIndexPage"]
    page_description = "A specific topic page. e.g. 'Public sector finance' or 'Inflation and price indices'."
    label = _("Topic")  # type: ignore[assignment]

    summary = RichTextField(features=settings.RICH_TEXT_BASIC)
    featured_series = models.ForeignKey(
        "articles.ArticleSeriesPage",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="featured_on_topic",
    )
    explore_more = StreamField(ExploreMoreStoryBlock(), blank=True)

    headline_figures = StreamField(
        [("figure", TopicHeadlineFigureBlock())],
        blank=True,
        max_num=6,
        help_text="Optional. If populating, it needs at least two headline figures.",
    )

    datasets = StreamField(DatasetStoryBlock(), blank=True, default=list, max_num=MAX_ITEMS_PER_SECTION)

    content_panels: ClassVar[list["Panel"]] = [
        *BundledPageMixin.panels,
        *BasePage.content_panels,
        FieldPanel("summary", required_on_save=True),
        FieldPanel("headline_figures"),
        FieldPanel(
            "featured_series",
            heading="Featured",
            widget=FeaturedSeriesPageChooserWidget(linked_fields={"topic_page_id": "#id_topic_page_id"}),
        ),
        InlinePanel(
            "related_articles",
            heading="Highlighted articles",
            help_text=(
                f"Choose up to {MAX_ITEMS_PER_SECTION} articles to highlight. "
                "The 'Related articles' section will be topped up automatically."
            ),
            max_num=MAX_ITEMS_PER_SECTION,
        ),
        FieldPanel(
            "datasets",
            heading="Datasets",
            help_text=f"Select up to {MAX_ITEMS_PER_SECTION} datasets related to this topic.",
            icon="doc-full",
        ),
        InlinePanel(
            "related_methodologies",
            heading="Highlighted methods and quality information",
            help_text=(
                f"Choose up to {MAX_ITEMS_PER_SECTION} methodologies to highlight. "
                "The 'Methods and quality information' section will be topped up automatically."
            ),
            max_num=MAX_ITEMS_PER_SECTION,
        ),
        FieldPanel("explore_more", heading="Explore more"),
    ]

    search_fields: ClassVar[list[index.BaseField]] = [*BasePage.search_fields, index.SearchField("summary")]

    def get_context(self, request: "HttpRequest", *args: Any, **kwargs: Any) -> dict:
        """Additional context for the template."""
        context: dict = super().get_context(request, *args, **kwargs)
        context["table_of_contents"] = self.table_of_contents
        context["featured_item"] = kwargs.get("featured_item", self.latest_article_in_featured_series)
        context["formatted_articles"] = get_formatted_pages_list(self.processed_articles, request=request)
        context["formatted_methodologies"] = get_formatted_pages_list(self.processed_methodologies, request=request)
        return context

    @cached_property
    def latest_article_in_featured_series(self) -> StatisticalArticlePage | None:
        """Returns the latest article in the featured series."""
        if self.featured_series:
            article: StatisticalArticlePage = (
                StatisticalArticlePage.objects.child_of(self.featured_series)
                .live()
                .public()
                .order_by("-release_date")
                .first()
            )
            return article
        return None

    @cached_property
    def processed_articles(self) -> list[ArticleSeriesPage]:
        """Returns the latest articles in the series relevant for this topic.
        TODO: extend when Taxonomy is in.
        """
        # check if any statistical articles were highlighted. if so, fetch in the order they were added.
        highlighted_page_pks = list(self.related_articles.values_list("page_id", flat=True))
        highlighted_pages = list(
            order_by_pk_position(
                StatisticalArticlePage.objects.live().public().defer_streamfields(),
                pks=highlighted_page_pks,
                exclude_non_matches=True,
            )
        )

        num_highlighted_pages = len(highlighted_pages)
        if num_highlighted_pages > MAX_ITEMS_PER_SECTION - 1:
            return highlighted_pages

        # supplement with the latest per series.
        newest_qs = (
            StatisticalArticlePage.objects.live()
            .public()
            .filter(path__startswith=OuterRef("path"), depth__gte=OuterRef("depth"))
        )
        newest_qs = newest_qs.order_by("-release_date")
        latest_by_series = (
            ArticleSeriesPage.objects.descendant_of(self)
            .annotate(latest_child_page=Subquery(newest_qs.values("pk")[:1]))
            .values_list("latest_child_page", flat=True)
        )
        latest_articles = list(
            StatisticalArticlePage.objects.filter(pk__in=latest_by_series)
            .exclude(pk__in=highlighted_pages)
            .live()
            .public()
            .defer_streamfields()
            .order_by("-release_date")[: MAX_ITEMS_PER_SECTION - num_highlighted_pages]
        )

        return highlighted_pages + latest_articles

    @cached_property
    def processed_methodologies(self) -> list[MethodologyPage]:
        """Returns the latest methodologies relevant for this topic.
        TODO: extend when Taxonomy is in.
        """
        # check if any methodologies were highlighted. if so, fetch in the order they were added.
        highlighted_page_pks = tuple(
            page_id for page_id in self.related_methodologies.values_list("page_id", flat=True)
        )
        highlighted_pages = list(
            order_by_pk_position(
                MethodologyPage.objects.live().public().defer_streamfields(),
                pks=highlighted_page_pks,
                exclude_non_matches=True,
            )
        )

        num_highlighted_pages = len(highlighted_pages)
        if num_highlighted_pages > MAX_ITEMS_PER_SECTION - 1:
            return highlighted_pages

        # supplement the remaining slots.
        pages = list(
            MethodologyPage.objects.descendant_of(self)
            .exclude(pk__in=highlighted_pages)
            .live()
            .public()
            .order_by("-last_revised_date")[: MAX_ITEMS_PER_SECTION - num_highlighted_pages]
        )
        return highlighted_pages + pages

    @cached_property
    def table_of_contents(self) -> list[dict[str, str | object]]:
        """Table of contents formatted to Design System specs."""
        items = []
        if self.latest_article_in_featured_series:
            items += [{"url": "#featured", "text": _("Featured")}]
        if self.processed_articles:  # pylint: disable=not-an-iterable,useless-suppression
            items += [{"url": "#related-articles", "text": _("Related articles")}]
        if self.processed_methodologies:
            items += [{"url": "#related-methods", "text": _("Methods and quality information")}]
        if self.explore_more:
            items += [{"url": "#explore-more", "text": _("Explore more")}]
        if self.dataset_document_list:
            items += [{"url": "#data", "text": _("Data")}]
        return items

    @cached_property
    def dataset_document_list(self) -> list[dict[str, Any]]:
        return format_datasets_as_document_list(self.datasets)

    def clean(self) -> None:
        super().clean()

        # Check if headline_figures has 1 item (we can't use min_num because we allow 0)
        if self.headline_figures:
            if len(self.headline_figures) == 1:
                raise ValidationError({"headline_figures": "If you add headline figures, please add at least 2."})

            figure_ids = [figure.value["figure_id"] for figure in self.headline_figures]  # pylint: disable=not-an-iterable
            if len(figure_ids) != len(set(figure_ids)):
                raise ValidationError({"headline_figures": "Duplicate headline figures are not allowed."})

    def permissions_for_user(self, user: "User") -> PagePermissionTester:
        """Overrides the core permissions_for_user to use our permission tester.

        TopicPagePermissionTester.can_add_subpage() takes into account max_count / max_count_per_parent.
        TODO: replaces when https://github.com/wagtail/wagtail/issues/13286 is fixed.
        """
        return TopicPagePermissionTester(user, self)
