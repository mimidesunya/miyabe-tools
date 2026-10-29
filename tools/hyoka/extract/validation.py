"""同梱 Schema の使用語彙だけを検証する。新しい依存部品は不要。

汎用の JSON Schema 実装ではない。未対応の制約を見つけたら起動時に停止する。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema/evaluation_record.schema.json"


class ValidationError(ValueError):
    pass


def validate(value, schema=None, root=None, path="$"):
    if schema is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if root is None:
        root = schema
    def fail(reason):
        raise ValidationError(f"{path}: {reason}")
    def check(v, s, p=path):
        validate(v, s, root, p)
    def matches(s):
        try:
            check(value,s)
            return True
        except ValidationError:
            return False
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            fail("external_ref_not_supported")
        resolved = root
        for key in ref[2:].split("/"):
            resolved = resolved[key.replace("~1","/").replace("~0","~")]
        check(value,resolved)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"],list) else [schema["type"]]
        predicates = {"object": lambda: isinstance(value,dict), "array": lambda: isinstance(value,list),
                      "string": lambda: isinstance(value,str), "null": lambda: value is None,
                      "boolean":lambda: isinstance(value,bool),
                      "integer":lambda: type(value) is int,
                      "number":lambda: type(value) in (int,float)}
        if not any(predicates[t]() for t in types): fail("type")
    if "const" in schema and value != schema["const"]: fail("const")
    if "enum" in schema and value not in schema["enum"]: fail("enum")
    for s in schema.get("allOf",[]): check(value,s)
    if "anyOf" in schema and not any(matches(s) for s in schema["anyOf"]): fail("anyOf")
    if "oneOf" in schema and sum(matches(s) for s in schema["oneOf"]) != 1: fail("oneOf")
    if "not" in schema and matches(schema["not"]): fail("not")
    if "if" in schema:
        branch = "then" if matches(schema["if"]) else "else"
        check(value,schema.get(branch,{}))
    if isinstance(value,dict):
        for name in schema.get("required",[]):
            if name not in value: fail("required:"+name)
        props = schema.get("properties",{})
        for name, v in value.items():
            if name in props: check(v,props[name],path+"."+name)
            elif schema.get("additionalProperties") is False: fail("additional:"+name)
    if isinstance(value,list):
        if len(value) < schema.get("minItems",0): fail("minItems")
        if len(value) > schema.get("maxItems",float("inf")): fail("maxItems")
        if schema.get("uniqueItems") and len({json.dumps(v,sort_keys=True) for v in value}) != len(value): fail("uniqueItems")
        for i,v in enumerate(value): check(v,schema.get("items",{}),f"{path}[{i}]")
    if isinstance(value,str):
        if len(value) < schema.get("minLength",0): fail("minLength")
        if "pattern" in schema and not re.search(schema["pattern"],value): fail("pattern")
        if schema.get("format") == "uri" and not urlsplit(value).scheme: fail("uri")
    if type(value) in (int,float):
        if value < schema.get("minimum",float("-inf")): fail("minimum")
        if value > schema.get("maximum",float("inf")): fail("maximum")


def check_schema_keywords(schema):
    allowed = {"$schema","$id","$ref","$defs","$comment","title","description","type","properties",
               "required","additionalProperties","enum","const","allOf","anyOf","oneOf","not",
               "if","then","else","items","minItems","maxItems","uniqueItems","minLength",
               "pattern","minimum","maximum","format"}
    for k,v in schema.items():
        if k.startswith("x-"): continue
        if k not in allowed: raise ValidationError("unsupported_schema_keyword:"+k)
        if k in {"properties","$defs"}:
            for s in v.values(): check_schema_keywords(s)
        elif k in {"allOf","anyOf","oneOf"}:
            for s in v: check_schema_keywords(s)
        elif k in {"if","then","else","not","items"}:
            check_schema_keywords(v)
