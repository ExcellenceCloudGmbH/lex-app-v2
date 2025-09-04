"""
Django Management Command: Setup CalculatedModelMixin Test Data

This command sets up complete test data for demonstrating and testing
the CalculatedModelMixin functionality.

Usage:
    python manage.py setup_calculated_model_test_data
    python manage.py setup_calculated_model_test_data --clear-only
    python manage.py setup_calculated_model_test_data --no-calculations
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, models
from django.db.models import Sum

from datetime import date, datetime, timedelta
from decimal import Decimal
import random
from typing import List
from django.db import transaction

from ArmiraCashflowDB.Examples._populate_test_data import populate_all_test_data

class Command(BaseCommand):
    help = 'Set up test data for CalculatedModelMixin examples'

    def handle(self, *args, **options):
        """Execute the command."""
        populate_all_test_data(True)



