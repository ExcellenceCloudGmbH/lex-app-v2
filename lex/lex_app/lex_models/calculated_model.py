import itertools
import logging
from copy import deepcopy

from celery.result import ResultSet
from django.db.models import Model, UniqueConstraint
from django.db.models.base import ModelBase

from lex_app import settings
from lex.lex_app.rest_api.context import context_id

logger = logging.getLogger(__name__)


def _flatten(list_2d):
    return list(itertools.chain.from_iterable(list_2d))


def calc_and_save_sync(models, *args):
    """
    Synchronous version of calc_and_save for fallback scenarios.
    """
    for model in models:
        model.calculate(*args)
        try:
            model.save()
        except Exception as e:
            old_model = model.delete_models_with_same_defining_fields()
            model.pk = old_model.pk
            model.save()


class CalculatedModelMixinMeta(ModelBase):
    def __new__(cls, name, bases, attrs, **kwargs):
        if 'Meta' not in attrs:
            class Meta:
                pass

            attrs['Meta'] = Meta

        if len(attrs['defining_fields']) != 0:
            attrs['Meta'].constraints = [
                UniqueConstraint(fields=attrs['defining_fields'], name='defining_fields_' + name)
            ]

        return super().__new__(cls, name, bases, attrs, **kwargs)


class CalculatedModelMixin(Model, metaclass=CalculatedModelMixinMeta):
    input = False
    defining_fields = []
    parallelizable_fields = []

    class Meta:
        abstract = True

    def get_selected_key_list(self, key: str) -> list:
        pass

    def calculate(self):
        pass


    @classmethod
    def _handle_celery_task_failure(cls, failed_groups, *args):
        """
        Handle failed Celery tasks by processing them synchronously.
        
        Args:
            failed_groups: List of model groups that failed in Celery
            *args: Arguments to pass to calculate method
        """
        logger.warning(f"Processing {len(failed_groups)} failed groups synchronously")
        for group in failed_groups:
            try:
                calc_and_save_sync(group, *args)
                logger.info(f"Successfully processed failed group of {len(group)} models synchronously")
            except Exception as sync_error:
                logger.error(f"Synchronous fallback also failed for group: {sync_error}")
                raise

    @classmethod
    def create(cls, *args, **kwargs):
        # define cls as base model
        models = [cls()]
        deleted = False
        # remove all the fields that are in the kwargs
        ordered_defining_fields = sorted(cls.defining_fields, key=lambda x: 0 if x in kwargs.keys() else 1)
        for field_name in ordered_defining_fields:
            field_name = field_name.__str__().split('.')[-1]
            i_temp_models = []
            # create new models from existing model by applying new selected key list
            for i, model in enumerate(models):
                if field_name in kwargs.keys():
                    selected_keys = kwargs[field_name]
                else:
                    selected_keys = model.get_selected_key_list(field_name)

                j_temp_models = [deepcopy(model) for i in range(len(selected_keys))]
                for j, fk in enumerate(selected_keys):
                    setattr(j_temp_models[j], field_name, fk)
                i_temp_models.append(j_temp_models)
            models = _flatten(i_temp_models)

            """if not deleted and field_name not in kwargs:
                for model in models:
                    keys = kwargs.keys()
                    filter_keys = {}
                    for k in keys:
                        filter_keys[k] = getattr(model, k)

                    filtered_objects = cls.objects.filter(**filter_keys)
                    filtered_objects.delete()"""

        for i in range(0, len(models)):
            model = models[i]
            model = model.delete_models_with_same_defining_fields()

            models[i] = model

        model: CalculatedModelMixin
        cluster_dict = {}
        for model in models:
            local_dict = cluster_dict
            for parallel_cluster in cls.parallelizable_fields[:-1]:
                attribute = getattr(model, parallel_cluster, None)
                if getattr(model, parallel_cluster, None) in local_dict.keys():
                    local_dict = local_dict[attribute]
                else:
                    cluster_dict[getattr(model, parallel_cluster, None)] = {}
            attribute = getattr(model, cls.parallelizable_fields[-1], None) if len(cls.parallelizable_fields)>0 else None
            if attribute in local_dict.keys():
                local_dict[attribute].append(model)
            else:
                local_dict[attribute] = [model]

        def add_to_group(local_cluster, groups):
            for k, v in local_cluster.items():
                if isinstance(v, dict):
                    groups = add_to_group(v, groups)
                else:
                    groups.append(v)
            return groups

        if settings.CELERY_ACTIVE:
            groups = add_to_group(cluster_dict, [])
            try:
                # Import the Celery task here to avoid circular imports
                from lex_app.celery_tasks import calc_and_save
                
                # Extract calculation_id from context if available
                calculation_id = None
                try:
                    context = context_id.get()
                    if context and "calculation_id" in context:
                        calculation_id = context["calculation_id"]
                except Exception as e:
                    logger.warning(f"Could not get calculation_id from context: {e}")
                
                # Dispatch each group as a separate Celery task
                task_results = []
                group_mapping = {}  # Map task results to their corresponding groups
                
                for i, group in enumerate(groups):
                    if group:  # Only dispatch non-empty groups
                        try:
                            task_result = calc_and_save.delay(group, *args, calculation_id=calculation_id)
                            task_results.append(task_result)
                            group_mapping[task_result.id] = group
                            logger.info(f"Dispatched Celery task {task_result.id} for group {i+1} of {len(group)} models with calculation_id: {calculation_id}")
                        except Exception as dispatch_error:
                            logger.error(f"Failed to dispatch task for group {i+1}: {dispatch_error}")
                            # Process this group synchronously as fallback
                            calc_and_save_sync(group, *args)
                
                if task_results:
                    # Create ResultSet from the task results and wait for completion
                    rs = ResultSet(task_results)
                    failed_groups = []
                    
                    try:
                        # Wait for all tasks to complete, but handle individual failures
                        results = rs.join(propagate=False)  # Don't propagate exceptions immediately
                        
                        # Check each task result for failures
                        for task_result in task_results:
                            try:
                                if task_result.failed():
                                    logger.error(f"Task {task_result.id} failed: {task_result.result}")
                                    # Add the corresponding group to failed_groups for retry
                                    if task_result.id in group_mapping:
                                        failed_groups.append(group_mapping[task_result.id])
                                else:
                                    logger.debug(f"Task {task_result.id} completed successfully")
                            except Exception as check_error:
                                logger.error(f"Error checking task {task_result.id} status: {check_error}")
                                # Assume failure and add to retry list
                                if task_result.id in group_mapping:
                                    failed_groups.append(group_mapping[task_result.id])
                        
                        # Process any failed groups synchronously
                        if failed_groups:
                            logger.warning(f"Processing {len(failed_groups)} failed task groups synchronously")
                            cls._handle_celery_task_failure(failed_groups, *args)
                        
                        successful_tasks = len(task_results) - len(failed_groups)
                        logger.info(f"Celery processing completed: {successful_tasks}/{len(task_results)} tasks successful")
                        
                    except Exception as celery_error:
                        logger.error(f"Celery ResultSet processing failed: {celery_error}")
                        # Fall back to synchronous processing for all groups
                        logger.warning("Falling back to synchronous processing for all groups")
                        calc_and_save_sync(models, *args)
                else:
                    logger.info("No groups to process, skipping Celery dispatch")
                    
            except Exception as celery_setup_error:
                logger.error(f"Celery setup failed: {celery_setup_error}")
                logger.warning("Falling back to synchronous processing")
                calc_and_save_sync(models, *args)
        else:
            logger.info("Celery not active, using synchronous processing")
            calc_and_save_sync(models, *args)

    def delete_models_with_same_defining_fields(self):
        filter_keys = {}
        for k in self.defining_fields:
            filter_keys[k] = getattr(self, k)
        filtered_objects = type(self).objects.filter(**filter_keys)
        if filtered_objects.count() == 1:
            model = filtered_objects.first()
        elif filtered_objects.count() == 0:
            # we do not modify the list
            model = self
        else:
            raise Exception(f"More than 1 object found for {self} with {filter_keys}")
        return model