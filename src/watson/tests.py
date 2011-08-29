"""
Tests for django-watson.

Fun fact: The MySQL full text search engine does not support indexing of words
that are 3 letters or fewer. Thus, the standard metasyntactic variables in
these tests have been amended to 'fooo' and 'baar'. Ho hum.
"""

from unittest import skipIf, skipUnless

from django.db import models
from django.test import TestCase
from django.core.management import call_command
from django.conf.urls.defaults import *
from django.contrib import admin
from django.contrib.auth.models import User

import watson
from watson.registration import RegistrationError, get_backend, SearchEngine
from watson.models import SearchEntry
from watson.admin import WatsonSearchAdmin


class TestModelBase(models.Model):

    title = models.CharField(
        max_length = 200,
    )
    
    content = models.TextField(
        blank = True,
    )
    
    description = models.TextField(
        blank = True,
    )
    
    is_published = models.BooleanField(
        default = True,
    )
    
    def __unicode__(self):
        return self.title

    class Meta:
        abstract = True
        app_label = "auth"  # Hack: Cannot use an app_label that is under South control, due to http://south.aeracode.org/ticket/520
        
        
class TestModel1(TestModelBase):

    pass


str_pk_gen = 0;

def get_str_pk():
    global str_pk_gen
    str_pk_gen += 1;
    return str(str_pk_gen)
    
    
class TestModel2(TestModelBase):

    id = models.CharField(
        primary_key = True,
        max_length = 100,
        default = get_str_pk
    )


class RegistrationTest(TestCase):
    
    def testRegistration(self):
        # Register the model and test.
        watson.register(TestModel1)
        self.assertTrue(watson.is_registered(TestModel1))
        self.assertRaises(RegistrationError, lambda: watson.register(TestModel1))
        self.assertTrue(TestModel1 in watson.get_registered_models())
        self.assertTrue(isinstance(watson.get_adapter(TestModel1), watson.SearchAdapter))
        # Unregister the model and text.
        watson.unregister(TestModel1)
        self.assertFalse(watson.is_registered(TestModel1))
        self.assertRaises(RegistrationError, lambda: watson.unregister(TestModel1))
        self.assertTrue(TestModel1 not in watson.get_registered_models())
        self.assertRaises(RegistrationError, lambda: isinstance(watson.get_adapter(TestModel1)))


complex_registration_search_engine = SearchEngine("restricted")


class SearchTestBase(TestCase):

    model1 = TestModel1
    
    model2 = TestModel2

    @watson.update_index
    def setUp(self):
        # Remove all the current registered models.
        self.registered_models = watson.get_registered_models()
        for model in self.registered_models:
            watson.unregister(model)
        # Register the test models.
        watson.register(self.model1)
        watson.register(self.model2, exclude=("id",))
        complex_registration_search_engine.register(TestModel1, exclude=("content", "description",), store=("is_published",))
        complex_registration_search_engine.register(TestModel2, fields=("title",))
        # Create some test models.
        self.test11 = TestModel1.objects.create(
            title = "title model1 instance11",
            content = "content model1 instance11",
            description = "description model1 instance11",
        )
        self.test12 = TestModel1.objects.create(
            title = "title model1 instance12",
            content = "content model1 instance12",
            description = "description model1 instance12",
        )
        self.test21 = TestModel2.objects.create(
            title = "title model2 instance21",
            content = "content model2 instance21",
            description = "description model2 instance21",
        )
        self.test22 = TestModel2.objects.create(
            title = "title model2 instance22",
            content = "content model2 instance22",
            description = "description model2 instance22",
        )

    def tearDown(self):
        # Re-register the old registered models.
        for model in self.registered_models:
            watson.register(model)
        # Unregister the test models.
        watson.unregister(self.model1)
        watson.unregister(self.model2)
        complex_registration_search_engine.unregister(TestModel1)
        complex_registration_search_engine.unregister(TestModel2)
        # Delete the test models.
        TestModel1.objects.all().delete()
        TestModel2.objects.all().delete()
        del self.test11
        del self.test12
        del self.test21
        del self.test22
        # Delete the search index.
        SearchEntry.objects.all().delete()


class InternalsTest(SearchTestBase):

    def testSearchEntriesCreated(self):
        self.assertEqual(SearchEntry.objects.filter(engine_slug="default").count(), 4)
        
    def testBuildWatsonCommand(self):
        # This update won't take affect, because no search context is active.
        self.test11.title = "fooo"
        self.test11.save()
        # Test that no update has happened.
        self.assertEqual(watson.search("fooo").count(), 0)
        # Run the rebuild command.
        call_command("buildwatson", verbosity=0)
        # Test that the update is now applies.
        self.assertEqual(watson.search("fooo").count(), 1)
        
    def testUpdateSearchIndex(self):
        # Update a model and make sure that the search results match.
        with watson.context():
            self.test11.title = "fooo"
            self.test11.save()
        # Test a search that should get one model.
        exact_search = watson.search("fooo")
        self.assertEqual(len(exact_search), 1)
        self.assertEqual(exact_search[0].title, "fooo")
        # Delete a model and make sure that the search results match.
        self.test11.delete()
        self.assertEqual(watson.search("fooo").count(), 0)
        
    def testFixesDuplicateSearchEntries(self):
        search_entries = SearchEntry.objects.filter(engine_slug="default")
        # Duplicate a couple of search entries.
        for search_entry in search_entries.all()[:2]:
            search_entry.id = None
            search_entry.save()
        # Make sure that we have six (including duplicates).
        self.assertEqual(search_entries.all().count(), 6)
        # Run the rebuild command.
        call_command("buildwatson", verbosity=0)
        # Make sure that we have four again (including duplicates).
        self.assertEqual(search_entries.all().count(), 4)
    
    def testSearchEmailParts(self):
        with watson.context():
            self.test11.content = "fooo@baar.com"
            self.test11.save()
        self.assertEqual(watson.search("fooo").count(), 1)
        self.assertEqual(watson.search("baar.com").count(), 1)
        self.assertEqual(watson.search("fooo@baar.com").count(), 1)
        
    def testFilter(self):
        for model in (TestModel1, TestModel2):
            # Test can find all.
            self.assertEqual(watson.filter(model, "TITLE").count(), 2)
        # Test can find a specific one.
        obj = watson.filter(TestModel1, "INSTANCE12").get()
        self.assertTrue(isinstance(obj, TestModel1))
        self.assertEqual(obj.title, "title model1 instance12")
        # Test can do filter on a queryset.
        obj = watson.filter(TestModel1.objects.filter(title__icontains="TITLE"), "INSTANCE12").get()
        self.assertTrue(isinstance(obj, TestModel1))
        self.assertEqual(obj.title, "title model1 instance12")
        
        
class SearchTest(SearchTestBase):
    
    def testMultiTableSearch(self):
        # Test a search that should get all models.
        self.assertEqual(watson.search("TITLE").count(), 4)
        self.assertEqual(watson.search("CONTENT").count(), 4)
        self.assertEqual(watson.search("DESCRIPTION").count(), 4)
        self.assertEqual(watson.search("TITLE CONTENT DESCRIPTION").count(), 4)
        # Test a search that should get two models.
        self.assertEqual(watson.search("MODEL1").count(), 2)
        self.assertEqual(watson.search("MODEL2").count(), 2)
        self.assertEqual(watson.search("TITLE MODEL1").count(), 2)
        self.assertEqual(watson.search("TITLE MODEL2").count(), 2)
        # Test a search that should get one model.
        self.assertEqual(watson.search("INSTANCE11").count(), 1)
        self.assertEqual(watson.search("INSTANCE21").count(), 1)
        self.assertEqual(watson.search("TITLE INSTANCE11").count(), 1)
        self.assertEqual(watson.search("TITLE INSTANCE21").count(), 1)
        # Test a search that should get zero models.
        self.assertEqual(watson.search("FOOO").count(), 0)
        self.assertEqual(watson.search("FOOO INSTANCE11").count(), 0)
        self.assertEqual(watson.search("MODEL2 INSTANCE11").count(), 0)
    
    def testLimitedModelList(self):
        # Test a search that should get all models.
        self.assertEqual(watson.search("TITLE", models=(TestModel1, TestModel2)).count(), 4)
        # Test a search that should get two models.
        self.assertEqual(watson.search("MODEL1", models=(TestModel1, TestModel2)).count(), 2)
        self.assertEqual(watson.search("MODEL1", models=(TestModel1,)).count(), 2)
        self.assertEqual(watson.search("MODEL2", models=(TestModel1, TestModel2)).count(), 2)
        self.assertEqual(watson.search("MODEL2", models=(TestModel2,)).count(), 2)
        # Test a search that should get one model.
        self.assertEqual(watson.search("INSTANCE11", models=(TestModel1, TestModel2)).count(), 1)
        self.assertEqual(watson.search("INSTANCE11", models=(TestModel1,)).count(), 1)
        self.assertEqual(watson.search("INSTANCE21", models=(TestModel1, TestModel2,)).count(), 1)
        self.assertEqual(watson.search("INSTANCE21", models=(TestModel2,)).count(), 1)
        # Test a search that should get zero models.
        self.assertEqual(watson.search("MODEL1", models=(TestModel2,)).count(), 0)
        self.assertEqual(watson.search("MODEL2", models=(TestModel1,)).count(), 0)
        self.assertEqual(watson.search("INSTANCE21", models=(TestModel1,)).count(), 0)
        self.assertEqual(watson.search("INSTANCE11", models=(TestModel2,)).count(), 0)
        
    def testExcludedModelList(self):
        # Test a search that should get all models.
        self.assertEqual(watson.search("TITLE", exclude=()).count(), 4)
        # Test a search that should get two models.
        self.assertEqual(watson.search("MODEL1", exclude=()).count(), 2)
        self.assertEqual(watson.search("MODEL1", exclude=(TestModel2,)).count(), 2)
        self.assertEqual(watson.search("MODEL2", exclude=()).count(), 2)
        self.assertEqual(watson.search("MODEL2", exclude=(TestModel1,)).count(), 2)
        # Test a search that should get one model.
        self.assertEqual(watson.search("INSTANCE11", exclude=()).count(), 1)
        self.assertEqual(watson.search("INSTANCE11", exclude=(TestModel2,)).count(), 1)
        self.assertEqual(watson.search("INSTANCE21", exclude=()).count(), 1)
        self.assertEqual(watson.search("INSTANCE21", exclude=(TestModel1,)).count(), 1)
        # Test a search that should get zero models.
        self.assertEqual(watson.search("MODEL1", exclude=(TestModel1,)).count(), 0)
        self.assertEqual(watson.search("MODEL2", exclude=(TestModel2,)).count(), 0)
        self.assertEqual(watson.search("INSTANCE21", exclude=(TestModel2,)).count(), 0)
        self.assertEqual(watson.search("INSTANCE11", exclude=(TestModel1,)).count(), 0)

    def testLimitedModelQuerySet(self):
        # Test a search that should get all models.
        self.assertEqual(watson.search("TITLE", models=(TestModel1.objects.filter(title__icontains="TITLE"), TestModel2.objects.filter(title__icontains="TITLE"),)).count(), 4)
        # Test a search that should get two models.
        self.assertEqual(watson.search("MODEL1", models=(TestModel1.objects.filter(
            title__icontains = "MODEL1",
            description__icontains = "MODEL1",
        ),)).count(), 2)
        self.assertEqual(watson.search("MODEL2", models=(TestModel2.objects.filter(
            title__icontains = "MODEL2",
            description__icontains = "MODEL2",
        ),)).count(), 2)
        # Test a search that should get one model.
        self.assertEqual(watson.search("INSTANCE11", models=(TestModel1.objects.filter(
            title__icontains = "MODEL1",
        ),)).count(), 1)
        self.assertEqual(watson.search("INSTANCE21", models=(TestModel2.objects.filter(
            title__icontains = "MODEL2",
        ),)).count(), 1)
        # Test a search that should get no models.
        self.assertEqual(watson.search("INSTANCE11", models=(TestModel1.objects.filter(
            title__icontains = "MODEL2",
        ),)).count(), 0)
        self.assertEqual(watson.search("INSTANCE21", models=(TestModel2.objects.filter(
            title__icontains = "MODEL1",
        ),)).count(), 0)
        
    def testExcludedModelQuerySet(self):
        # Test a search that should get all models.
        self.assertEqual(watson.search("TITLE", exclude=(TestModel1.objects.filter(title__icontains="FOOO"), TestModel2.objects.filter(title__icontains="FOOO"),)).count(), 4)
        # Test a search that should get two models.
        self.assertEqual(watson.search("MODEL1", exclude=(TestModel1.objects.filter(
            title__icontains = "INSTANCE21",
            description__icontains = "INSTANCE22",
        ),)).count(), 2)
        self.assertEqual(watson.search("MODEL2", exclude=(TestModel2.objects.filter(
            title__icontains = "INSTANCE11",
            description__icontains = "INSTANCE12",
        ),)).count(), 2)
        # Test a search that should get one model.
        self.assertEqual(watson.search("INSTANCE11", exclude=(TestModel1.objects.filter(
            title__icontains = "MODEL2",
        ),)).count(), 1)
        self.assertEqual(watson.search("INSTANCE21", exclude=(TestModel2.objects.filter(
            title__icontains = "MODEL1",
        ),)).count(), 1)
        # Test a search that should get no models.
        self.assertEqual(watson.search("INSTANCE11", exclude=(TestModel1.objects.filter(
            title__icontains = "MODEL1",
        ),)).count(), 0)
        self.assertEqual(watson.search("INSTANCE21", exclude=(TestModel2.objects.filter(
            title__icontains = "MODEL2",
        ),)).count(), 0)
        
    def testKitchenSink(self):
        """For sanity, let's just test everything together in one giant search of doom!"""
        results = self.assertEqual(watson.search(
            "INSTANCE11",
            models = (
                TestModel1.objects.filter(title__icontains="INSTANCE11"),
                TestModel2.objects.filter(title__icontains="TITLE"),
            ),
            exclude = (
                TestModel1.objects.filter(title__icontains="MODEL2"),
                TestModel2.objects.filter(title__icontains="MODEL1"),
            )
        ).get().title, "title model1 instance11")
        
        
class LiveFilterSearchTest(SearchTest):
    
    model1 = TestModel1.objects.filter(is_published=True)
    
    model2 = TestModel2.objects.filter(is_published=True)
    
    def testUnpublishedModelsNotFound(self):
        # Make sure that there are four to find!
        self.assertEqual(watson.search("tItle Content Description").count(), 4)
        # Unpublish two objects.
        with watson.context():
            self.test11.is_published = False
            self.test11.save()
            self.test21.is_published = False
            self.test21.save()
        # This should return 4, but two of them are unpublished.
        self.assertEqual(watson.search("tItle Content Description").count(), 2)
        
    def testCanOverridePublication(self):
        # Unpublish two objects.
        with watson.context():
            self.test11.is_published = False
            self.test11.save()
        # This should still return 4, since we're overriding the publication.
        self.assertEqual(watson.search("tItle Content Description", models=(TestModel2, TestModel1._base_manager.all(),)).count(), 4)
        
        
class RankingTest(SearchTestBase):

    @watson.update_index
    def setUp(self):
        super(RankingTest, self).setUp()
        self.test11.title += " fooo baar fooo"
        self.test11.save()
        self.test12.title += " fooo baar"
        self.test12.save()

    def testRankingParamPresentOnSearch(self):
        self.assertGreater(watson.search("TITLE")[0].watson_rank, 0)
        
    def testRankingParamPresentOnFilter(self):
        self.assertGreater(watson.filter(TestModel1, "TITLE")[0].watson_rank, 0)
        
    def testRankingParamAbsentOnSearch(self):
        self.assertRaises(AttributeError, lambda: watson.search("TITLE", ranking=False)[0].watson_rank)
        
    def testRankingParamAbsentOnFilter(self):
        self.assertRaises(AttributeError, lambda: watson.filter(TestModel1, "TITLE", ranking=False)[0].watson_rank)
    
    @skipUnless(get_backend().supports_ranking, "search backend does not support ranking")
    def testRankingWithSearch(self):
        self.assertEqual(
            [entry.title for entry in watson.search("FOOO")],
            [u"title model1 instance11 fooo baar fooo", u"title model1 instance12 fooo baar"]
        )
            
    @skipUnless(get_backend().supports_ranking, "search backend does not support ranking")
    def testRankingWithFilter(self):
        self.assertEqual(
            [entry.title for entry in watson.filter(TestModel1, "FOOO")],
            [u"title model1 instance11 fooo baar fooo", u"title model1 instance12 fooo baar"]
        )


class ComplexRegistrationTest(SearchTestBase):

    def testMetaStored(self):
        self.assertEqual(complex_registration_search_engine.search("instance11")[0].meta["is_published"], True)
        
    def testMetaNotStored(self):
        self.assertRaises(KeyError, lambda: complex_registration_search_engine.search("instance21")[0].meta["is_published"])
        
    def testFieldsExcludedOnSearch(self):
        self.assertEqual(complex_registration_search_engine.search("TITLE").count(), 4)
        self.assertEqual(complex_registration_search_engine.search("CONTENT").count(), 0)
        self.assertEqual(complex_registration_search_engine.search("DESCRIPTION").count(), 0)
        
    def testFieldsExcludedOnFilter(self):
        self.assertEqual(complex_registration_search_engine.filter(TestModel1, "TITLE").count(), 2)
        self.assertEqual(complex_registration_search_engine.filter(TestModel1, "CONTENT").count(), 0)
        self.assertEqual(complex_registration_search_engine.filter(TestModel1, "DESCRIPTION").count(), 0)
        self.assertEqual(complex_registration_search_engine.filter(TestModel2, "TITLE").count(), 2)
        self.assertEqual(complex_registration_search_engine.filter(TestModel2, "CONTENT").count(), 0)
        self.assertEqual(complex_registration_search_engine.filter(TestModel2, "DESCRIPTION").count(), 0)


class TestModel1Admin(WatsonSearchAdmin):

    search_fields = ("title", "description", "content",)
    
    list_display = ("title",)
    
    
admin.site.register(TestModel1, TestModel1Admin)


urlpatterns = patterns("watson.views",

    url("^simple/$", "search", name="search_simple"),
    
    url("^custom/$", "search", name="search_custom", kwargs={
        "query_param": "fooo",
        "empty_query_redirect": "/simple/",
    }),
    
    url("^admin/", include(admin.site.urls)),

)


class AdminIntegrationTest(SearchTestBase):

    urls = "watson.tests"
    
    def setUp(self):
        super(AdminIntegrationTest, self).setUp()
        self.user = User(
            username = "foo",
            is_staff = True,
            is_superuser = True,
        )
        self.user.set_password("bar")
        self.user.save()
    
    def testAdminIntegration(self):
        self.client.login(username="foo", password="bar")
        # Test a search for all the instances.
        response = self.client.get("/admin/auth/testmodel1/?q=title content description")
        self.assertContains(response, "instance11")
        self.assertContains(response, "instance12")
        # Test a search for half the instances.
        response = self.client.get("/admin/auth/testmodel1/?q=instance11")
        self.assertContains(response, "instance11")
        self.assertNotContains(response, "instance12")
        
    def tearDown(self):
        super(AdminIntegrationTest, self).tearDown()
        self.user.delete()
        del self.user
        
        
class SiteSearchTest(SearchTestBase):

    urls = "watson.tests"
    
    def testSiteSearch(self):
        # Test a search than should find everything.
        response = self.client.get("/simple/?q=title")
        self.assertContains(response, "instance11")
        self.assertContains(response, "instance12")
        self.assertContains(response, "instance21")
        self.assertContains(response, "instance22")
        self.assertTemplateUsed(response, "watson/result_list.html")
        # Test a search that should find one thing.
        response = self.client.get("/simple/?q=instance11")
        self.assertContains(response, "instance11")
        self.assertNotContains(response, "instance12")
        self.assertNotContains(response, "instance21")
        self.assertNotContains(response, "instance22")
        # Test a search that should find nothing.
        response = self.client.get("/simple/?q=fooo")
        self.assertNotContains(response, "instance11")
        self.assertNotContains(response, "instance12")
        self.assertNotContains(response, "instance21")
        self.assertNotContains(response, "instance22")
        
    def testSiteSearchCustom(self):
        # Test a search than should find everything.
        response = self.client.get("/custom/?fooo=title")
        self.assertContains(response, "instance11")
        self.assertContains(response, "instance12")
        self.assertContains(response, "instance21")
        self.assertContains(response, "instance22")
        self.assertTemplateUsed(response, "watson/result_list.html")
        # Test a search that should find nothing.
        response = self.client.get("/custom/?q=fooo")
        self.assertRedirects(response, "/simple/")