# -*- coding: UTF-8 -*-
from __future__ import unicode_literals

import logging

from collections import defaultdict

from django.utils.module_loading import autodiscover_modules
from django.db.models import signals

logger = logging.getLogger(__name__)


class MonitoringHandler(object):
    def __init__(self):
        self._registered_checks = {}
        self._related_models = defaultdict(list)

    def autodiscover_checks(self, module_name='checks'):
        autodiscover_modules(module_name)

    def register(self, check_class):
        slug = self.get_slug(check_class.__module__, check_class.__name__)
        self._registered_checks[slug] = check_class
        check = check_class()
        if hasattr(check, 'trigger_update'):
            for method_name, model in check.trigger_update.items():
                if not hasattr(check, 'get_%s_payload' % method_name):
                    logger.warning('Update trigger defined without implementing .get_*_payload()')
                    continue

                model_uid = make_model_uid(model)
                if model_uid in self._related_models:
                    signals.post_save.connect(run_checks, sender=model)
                self._related_models[model_uid].append(check_class)

        return check_class

    def get_all_registered_checks(self):
        return self._registered_checks.values()

    @property
    def checks(self):
        for slug in self._registered_checks.keys():
            yield slug

    def get_check_class(self, slug):
        if slug in self._registered_checks:
            return self._registered_checks[slug]
        return None

    def get_checks_for_model(self, model):
        model_uid = make_model_uid(model)
        if model_uid in self._related_models:
            return self._related_models[model_uid]
        return None

    def get_slug(self, module, class_name):
        return u'{}.{}'.format(module, class_name)


monitor = MonitoringHandler()


def make_model_uid(model):
    """
    Returns an uid that will identify the given model class.

    :param model: model class
    :return: uid (string)
    """
    return "%s.%s" % (model._meta.app_label, model.__name__)


def run_checks(sender, instance, created, raw, using, **kwargs):
    """
    Re-execute checks related to the given sender model, only for the updated instance.

    :param sender: model
    :param kwargs:
    """
    from django_monitoring.tasks import django_monitoring_run
    checks = monitor.get_checks_for_model(sender) or []
    for check_class in checks:
        check = check_class()
        payload = check.get_payload(instance)
        if not payload:
            continue
        django_monitoring_run().apply(
            kwargs=dict(check_slug=check.slug, identifier=check.get_identifier(payload)),
            queue='django_monitoring')
