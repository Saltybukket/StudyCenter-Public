"""Bounded schema validation with document-local JSON pointers only.

Expansion eliminates references from the validator input, so no resolver can
perform an implicit network request. Remote scopes, dynamic references, dangling
pointers and recursive graphs fail closed before sending source material.
"""
import copy
import json
import re
from datetime import datetime, time
from urllib.parse import unquote

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

MAX_NODES = 10000
MAX_DEPTH = 64
MAX_BYTES = 250000


def model_format_checker():
    checker = FormatChecker()

    @checker.checks('studycenter-local-time')
    def local_clock(value):
        if not isinstance(value, str): return True
        if not re.fullmatch(r'[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?', value):
            return False
        try: return time.fromisoformat(value).tzinfo is None
        except ValueError: return False

    @checker.checks('date-time')
    def rfc3339_datetime(value):
        # jsonschema makes its RFC validator optional. Enforce the contract even
        # in a frozen package where that optional dependency is not installed.
        if not isinstance(value, str): return True
        if len(value) > 64 or not re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}'
            r'(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])', value):
            return False
        try:
            normalized = value.replace('t', 'T').replace('z', 'Z')
            parsed = datetime.fromisoformat(normalized)
            return parsed.tzinfo is not None
        except ValueError: return False

    return checker


def local_schema(schema):
    if not isinstance(schema, (dict, bool)):
        raise SchemaError('A JSON schema object is required')
    visited = 0

    def pointer(reference):
        if not isinstance(reference, str) or not reference.startswith('#/'):
            raise SchemaError('Only document-local JSON pointers are supported')
        target = schema
        for part in unquote(reference[2:]).split('/'):
            if '~' in part.replace('~0','').replace('~1',''):
                raise SchemaError('Malformed JSON pointer')
            key = part.replace('~1','/').replace('~0','~')
            try:
                if isinstance(target, list):
                    if not key.isdecimal() or (len(key)>1 and key.startswith('0')):
                        raise KeyError(key)
                    target = target[int(key)]
                elif isinstance(target, dict): target = target[key]
                else: raise KeyError(key)
            except (KeyError, IndexError, ValueError):
                raise SchemaError('Unresolved local JSON pointer') from None
        if not isinstance(target, (dict, bool)):
            raise SchemaError('Reference target must be a schema')
        return target

    def expand(node, depth=0, stack=()):
        nonlocal visited
        visited += 1
        if visited > MAX_NODES or depth > MAX_DEPTH:
            raise SchemaError('Schema expansion exceeds bounds')
        if isinstance(node, list):
            return [expand(value,depth+1,stack) for value in node]
        if not isinstance(node, dict): return node
        if '$id' in node or '$dynamicRef' in node or '$recursiveRef' in node or '$dynamicAnchor' in node:
            raise SchemaError('Schema scope changes and dynamic references are unsupported')
        if '$schema' in node and node['$schema'] != 'https://json-schema.org/draft/2020-12/schema':
            raise SchemaError('Unsupported schema dialect')
        if '$ref' in node:
            ref=node['$ref']
            if ref in stack: raise SchemaError('Recursive schema reference')
            resolved=expand(pointer(ref),depth+1,stack+(ref,))
            siblings={key:value for key,value in node.items() if key!='$ref'}
            if not siblings: return resolved
            return {'allOf':[resolved,expand(siblings,depth+1,stack)]}
        result = {}
        for key, value in node.items():
            if key in ('properties', '$defs', 'patternProperties', 'dependentSchemas'):
                if not isinstance(value, dict): raise SchemaError('Schema map required')
                result[key] = {name:expand(definition,depth+1,stack) for name,definition in value.items()}
            elif key in ('const', 'enum', 'default', 'examples', 'required', 'dependentRequired'):
                # Values and property names are data, not nested schema documents.
                result[key] = copy.deepcopy(value)
            else:
                result[key] = expand(value,depth+1,stack)
        return result

    resolved=expand(schema)
    if len(json.dumps(resolved,ensure_ascii=False).encode()) > MAX_BYTES:
        raise SchemaError('Expanded schema exceeds bounds')
    Draft202012Validator.check_schema(resolved)
    return resolved


def validator_for(schema):
    return Draft202012Validator(local_schema(schema), format_checker=model_format_checker())


def strict_openai_schema(schema):
    """Only claim provider strict support for a small fully known safe subset."""
    def supported(node):
        if not isinstance(node, dict): return False
        if set(node)-{'type','properties','required','additionalProperties','items','enum','title','description'}:
            return False
        kind=node.get('type')
        if kind=='object':
            properties=node.get('properties')
            required=node.get('required')
            return (isinstance(properties,dict) and isinstance(required,list)
                    and node.get('additionalProperties') is False
                    and len(required)==len(set(required)) and set(required)==set(properties)
                    and all(supported(value) for value in properties.values()))
        if kind=='array': return supported(node.get('items'))
        if kind not in ('string','number','integer','boolean','null'): return False
        return not any(key in node for key in ('properties','required','additionalProperties','items'))
    return isinstance(schema,dict) and schema.get('type')=='object' and supported(schema)
