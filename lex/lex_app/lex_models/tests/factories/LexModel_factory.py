"""
Factory for LexModel testing
"""

import factory
from django.db import models
from lex.lex_app.lex_models.LexModel import LexModel


class DummyLexModel(LexModel):
    """Dummy model for testing LexModel functionality."""
    name = models.CharField(max_length=100)
    value = models.IntegerField(default=0)
    
    class Meta:
        app_label = 'lex_app'


class LexModelFactory(factory.django.DjangoModelFactory):
    """Factory for creating LexModel instances in tests."""
    
    name = factory.Sequence(lambda n: f"Test Model {n}")
    value = factory.Faker('random_int', min=1, max=100)
    
    class Meta:
        model = DummyLexModel