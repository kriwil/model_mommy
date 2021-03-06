# -*- coding: utf-8 -*-
import warnings

from django.utils import importlib
from django.contrib.contenttypes import generic
from django.contrib.contenttypes.models import ContentType
from django.db.models.loading import cache, get_model
from django.db.models.base import ModelBase
from django.db.models import (\
    CharField, EmailField, SlugField, TextField, URLField,
    DateField, DateTimeField, TimeField,
    AutoField, IntegerField, SmallIntegerField,
    PositiveIntegerField, PositiveSmallIntegerField,
    BooleanField, DecimalField, FloatField,
    FileField, ImageField,
    ForeignKey, ManyToManyField, OneToOneField)

try:
    from django.db.models import BigIntegerField
except ImportError:
    BigIntegerField = IntegerField

import generators
from exceptions import ModelNotFound, AmbiguousModelName, InvalidQuantityException

recipes = None

# FIXME: use pkg_resource
from os.path import dirname, join
mock_file_jpeg = join(dirname(__file__), 'mock-img.jpeg')
mock_file_txt = join(dirname(__file__), 'mock_file.txt')


#TODO: improve related models handling
foreign_key_required = [lambda field: ('model', field.related.parent_model)]

MAX_MANY_QUANTITY = 5

def make(model, _quantity=None, make_m2m=False, **attrs):
    """
    Creates a persisted instance from a given model its associated models.
    It fill the fields with random values or you can specify
    which fields you want to define its values by yourself.
    """
    mommy = Mommy(model, make_m2m=make_m2m)
    if _quantity and (not isinstance(_quantity, int) or _quantity < 1):
        raise InvalidQuantityException

    if _quantity:
        return [mommy.make(**attrs) for i in range(_quantity)]
    else:
        return mommy.make(**attrs)


def prepare(model, _quantity=None, **attrs):
    """
    Creates a BUT DOESN'T persist an instance from a given model its
    associated models.
    It fill the fields with random values or you can specify
    which fields you want to define its values by yourself.
    """
    mommy = Mommy(model)
    if _quantity and (not isinstance(_quantity, int) or _quantity < 1):
        raise InvalidQuantityException

    if _quantity:
        return [mommy.prepare(**attrs) for i in range(_quantity)]
    else:
        return mommy.prepare(**attrs)


def _recipe(name):
    splited_name = name.split('.')
    app, recipe_name = '.'.join(splited_name[0:-1]), splited_name[-1]
    recipes = importlib.import_module('.'.join([app, 'mommy_recipes']))
    return getattr(recipes, recipe_name)

def make_recipe(mommy_recipe_name, _quantity=None, **new_attrs):
    return _recipe(mommy_recipe_name).make(_quantity=_quantity, **new_attrs)

def prepare_recipe(mommy_recipe_name, _quantity=None, **new_attrs):
    return _recipe(mommy_recipe_name).prepare(_quantity=_quantity, **new_attrs)


def __m2m_generator(model, **attrs):
    return make(model, _quantity=MAX_MANY_QUANTITY, **attrs)

make.required = foreign_key_required
prepare.required = foreign_key_required
__m2m_generator.required = foreign_key_required

default_mapping = {
    BooleanField: generators.gen_boolean,
    IntegerField: generators.gen_integer,
    BigIntegerField: generators.gen_integer,
    SmallIntegerField: generators.gen_integer,

    PositiveIntegerField: lambda: generators.gen_integer(0),
    PositiveSmallIntegerField: lambda: generators.gen_integer(0),

    FloatField: generators.gen_float,
    DecimalField: generators.gen_decimal,

    CharField: generators.gen_string,
    TextField: generators.gen_text,
    SlugField: generators.gen_slug,

    ForeignKey: make,
    OneToOneField: make,
    ManyToManyField: __m2m_generator,

    DateField: generators.gen_date,
    DateTimeField: generators.gen_datetime,
    TimeField: generators.gen_time,

    URLField: generators.gen_url,
    EmailField: generators.gen_email,
    FileField: generators.gen_file_field,
    ImageField: generators.gen_image_field,

    ContentType: generators.gen_content_type,
}


class ModelFinder(object):
    '''
    Encapsulates all the logic for finding a model to Mommy.
    '''
    _unique_models = None
    _ambiguous_models = None

    def get_model(self, name):
        '''
        Get a model.

        :param name String on the form 'applabel.modelname' or 'modelname'.
        :return a model class.
        '''
        if '.' in name:
            app_label, model_name = name.split('.')
            model =  get_model(app_label, model_name)
        else:
            model = self.get_model_by_name(name)

        if not model:
            raise ModelNotFound("Could not find model '%s'." % name.title())

        return model

    def get_model_by_name(self, name):
        '''
        Get a model by name.

        If a model with that name exists in more than one app,
        raises AmbiguousModelName.
        '''
        name = name.lower()

        if self._unique_models is None:
            self._populate()

        if name in self._ambiguous_models:
            raise AmbiguousModelName('%s is a model in more than one app. '
                                     'Use the form "app.model".' % name.title())

        return self._unique_models.get(name)

    def _populate(self):
        '''
        Cache models for faster self._get_model.
        '''
        unique_models = {}
        ambiguous_models = []

        for app_model in cache.app_models.values():
            for name, model in app_model.items():
                if name not in unique_models:
                    unique_models[name] = model
                else:
                    ambiguous_models.append(name)

        for name in ambiguous_models:
            unique_models.pop(name)

        self._ambiguous_models = ambiguous_models
        self._unique_models = unique_models


class Mommy(object):
    attr_mapping = {}
    type_mapping = None

    # Note: we're using one finder for all Mommy instances to avoid
    # rebuilding the model cache for every make_* or prepare_* call.
    finder = ModelFinder()

    def __init__(self, model, make_m2m=False):
        self.make_m2m = make_m2m
        self.m2m_dict = {}
        self.type_mapping = default_mapping.copy()

        if isinstance(model, ModelBase):
            self.model = model
        else:
            self.model = self.finder.get_model(model)

    def make(self, **attrs):
        '''Creates and persists an instance of the model
        associated with Mommy instance.'''
        return self._make(commit=True, **attrs)

    def prepare(self, **attrs):
        '''Creates, but do not persists, an instance of the model
        associated with Mommy instance.'''
        self.type_mapping[ForeignKey] = prepare
        return self._make(commit=False, **attrs)

    def get_fields(self):
        return self.model._meta.fields + self.model._meta.many_to_many

    def _make(self, commit=True, **attrs):
        is_fk_field = lambda x: '__' in x
        model_attrs = dict((k, v) for k, v in attrs.items() if not is_fk_field(k))
        fk_attrs = dict((k, v) for k, v in attrs.items() if is_fk_field(k))
        fk_fields = [x.split('__')[0] for x in fk_attrs.keys() if is_fk_field(x)]

        for field in self.get_fields():
            field_value_not_defined = field.name not in model_attrs

            if isinstance(field, (AutoField, generic.GenericRelation)):
                continue

            if isinstance(field, ForeignKey) and field.name in fk_fields:
                model_attrs[field.name] = self.generate_value(field, **fk_attrs)
                continue

            if not field.name in self.attr_mapping and (
                    (hasattr(field, 'has_default') and field.has_default())
                    or field.blank):
                continue

            if isinstance(field, ManyToManyField):
                if field.name not in model_attrs:
                    self.m2m_dict[field.name] = self.m2m_value(field)
                else:
                    self.m2m_dict[field.name] = model_attrs.pop(field.name)
            elif field_value_not_defined:
                if field.null:
                    continue
                else:
                    model_attrs[field.name] = self.generate_value(field)

        return self.instance(model_attrs, _commit=commit)

    def m2m_value(self, field):
        if not self.make_m2m or field.null:
            return []
        else:
            return self.generate_value(field)

    def instance(self, attrs, _commit):
        instance = self.model(**attrs)
        # m2m only works for persisted instances
        if _commit:
            instance.save()
            # m2m relation is treated differently
            for key, value in self.m2m_dict.items():
                m2m_relation = getattr(instance, key)
                for model_instance in value:
                    m2m_relation.add(model_instance)

        return instance

    def generate_value(self, field, **fk_attrs):
        '''
        Calls the generator associated with a field passing all required args.

        Generator Resolution Precedence Order:
        -- attr_mapping - mapping per attribute name
        -- choices -- mapping from avaiable field choices
        -- type_mapping - mapping from user defined type associated generators
        -- default_mapping - mapping from pre-defined type associated
           generators

        `attr_mapping` and `type_mapping` can be defined easely overwriting the
        model.
        '''
        if field.name in self.attr_mapping:
            generator = self.attr_mapping[field.name]
        elif getattr(field, 'choices'):
            generator = generators.gen_from_choices(field.choices)
        elif isinstance(field, ForeignKey) and field.rel.to is ContentType:
            generator = self.type_mapping[ContentType]
        elif field.__class__ in self.type_mapping:
            generator = self.type_mapping[field.__class__]
        else:
            raise TypeError('%s is not supported by mommy.' % field.__class__)

        # attributes like max_length, decimal_places are take in account when
        # generating the value.
        generator_attrs = get_required_values(generator, field)

        if isinstance(field, ForeignKey):
            generator_attrs.update(filter_fk_attrs(field.name, **fk_attrs))

        return generator(**generator_attrs)


def get_required_values(generator, field):
    '''
    Gets required values for a generator from the field.
    If required value is a function, call's it with field as argument.
    If required value is a string, simply fetch the value from the field
    and returns.
    '''
    #FIXME: avoid abreviations
    rt = {}
    if hasattr(generator, 'required'):
        for item in generator.required:

            if callable(item):  # mommy can deal with the nasty hacking too!
                key, value = item(field)
                rt[key] = value

            elif isinstance(item, basestring):
                rt[item] = getattr(field, item)

            else:
                raise ValueError("Required value '%s' is of wrong type. \
                                  Don't make mommy sad." % str(item))

    return rt

def filter_fk_attrs(field_name, **fk_attrs):
    clean_dict = {}

    for k, v in fk_attrs.items():
        if k.startswith(field_name + '__'):
            splited_key = k.split('__')
            key = '__'.join(splited_key[1:])
            clean_dict[key] = v
        else:
            clean_dict[k] = v

    return clean_dict


### DEPRECATED METHODS (should be removed on the future)
def make_many(model, quantity=None, **attrs):
    msg = "make_many is deprecated. You should use make with _quantity parameter."
    warnings.warn(msg, DeprecationWarning)
    quantity = quantity or MAX_MANY_QUANTITY
    mommy = Mommy(model)
    return [mommy.make(**attrs) for i in range(quantity)]


def make_one(model, make_m2m=False, **attrs):
    msg = "make_one is deprecated. You should use the method make instead."
    warnings.warn(msg, DeprecationWarning)
    mommy = Mommy(model, make_m2m=make_m2m)
    return mommy.make(**attrs)


def prepare_one(model, **attrs):
    msg = "prepare_one is deprecated. You should use the method prepare instead."
    warnings.warn(msg, DeprecationWarning)
    mommy = Mommy(model)
    return mommy.prepare(**attrs)


def make_many_from_recipe(mommy_recipe_name, quantity=None, **new_attrs):
    msg = "make_many_from_recipe is deprecated. You should use the method make_recipe with the _quantity parameter instead."
    warnings.warn(msg, DeprecationWarning)
    quantity = quantity or MAX_MANY_QUANTITY
    return [make_recipe(mommy_recipe_name, **new_attrs) for x in range(quantity)]
