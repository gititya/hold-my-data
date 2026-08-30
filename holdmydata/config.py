"""Load and validate the three config files.

The config is the product (PRD 5b). Code never hardcodes an entity. Validation is strict and
fails at startup, not halfway through a document.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_SOURCE_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_INSTALLED_CONFIG_DIR = Path(sys.prefix) / "config"
DEFAULT_CONFIG_DIR = (
    _SOURCE_CONFIG_DIR if _SOURCE_CONFIG_DIR.exists() else _INSTALLED_CONFIG_DIR
)

VALID_DETECTORS = {"regex", "gliner", "presidio", "both", "address_model"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
# Region tags let a context say "India only" or "medical only" without listing entities.
VALID_REGIONS = {
    "global", "india", "usa", "uk", "medical", "secrets", "meaning", "custom",
}
VALID_STRATEGIES = {"consistent", "label", "mask", "hash"}


class ConfigError(ValueError):
    """The config is wrong. Say exactly how, and stop."""


@dataclass
class Entity:
    name: str
    description: str
    detect_with: list
    severity: str
    replacement: str = None
    region: str = "custom"
    # Per-entity floor, overriding the context threshold. Needed because Presidio's own
    # recognisers are not score-calibrated against each other: IN_PAN scores a real PAN at
    # 0.45 and a 10-digit phone number at 0.40, so one global threshold cannot separate them.
    min_score: float = None

    @property
    def uses_regex(self) -> bool:
        return "regex" in self.detect_with or "both" in self.detect_with

    @property
    def uses_gliner(self) -> bool:
        return "gliner" in self.detect_with or "both" in self.detect_with

    @property
    def uses_presidio(self) -> bool:
        """Use Presidio's own predefined recogniser for this entity.

        Free, already validated (Aadhaar/PAN/SSN checksums), and someone else maintains it.
        The entity `name` must be Presidio's exact entity name, e.g. IN_AADHAAR.
        """
        return "presidio" in self.detect_with

    @property
    def uses_address_model(self) -> bool:
        """The dedicated Indian-address NER model (shiprocket-ai), not GLiNER.

        Validated 2026-08-18: 80% precision / 100% recall on real addresses, vs GLiNER's
        0% recall on the same set (evidence/eval-full-2026-08-18.md).
        """
        return "address_model" in self.detect_with


@dataclass
class Pattern:
    entity: str
    name: str
    regex: str
    score: float


@dataclass
class Context:
    name: str
    entities: list
    threshold: float
    strategy: str


@dataclass
class Config:
    entities: dict = field(default_factory=dict)
    patterns: list = field(default_factory=list)
    contexts: dict = field(default_factory=dict)

    def context(self, name: str) -> Context:
        if name not in self.contexts:
            known = ", ".join(sorted(self.contexts)) or "(none)"
            raise ConfigError(f"unknown context '{name}'. defined: {known}")
        return self.contexts[name]

    def patterns_for(self, entity: str) -> list:
        return [p for p in self.patterns if p.entity == entity]


def _read(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must be a mapping at the top level")
    return data


def _load_entities(raw: dict) -> dict:
    items = raw.get("entities")
    if not items:
        raise ConfigError("taxonomy.yaml defines no entities. Nothing can be detected.")

    entities = {}
    for i, item in enumerate(items):
        where = f"taxonomy.yaml entity #{i + 1}"
        name = item.get("name")
        if not name:
            raise ConfigError(f"{where}: missing 'name'")
        if name in entities:
            raise ConfigError(f"taxonomy.yaml: duplicate entity '{name}'")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ConfigError(f"{where}: '{name}' must be UPPER_SNAKE_CASE")

        description = (item.get("description") or "").strip()
        if not description:
            raise ConfigError(
                f"{where} ('{name}'): missing 'description'. GLiNER reads it verbatim, and "
                "you cannot detect a category you have not defined."
            )

        detect_with = item.get("detect_with") or []
        if isinstance(detect_with, str):
            detect_with = [detect_with]
        if not detect_with:
            raise ConfigError(f"{where} ('{name}'): 'detect_with' is empty")
        bad = set(detect_with) - VALID_DETECTORS
        if bad:
            raise ConfigError(
                f"{where} ('{name}'): unknown detect_with {sorted(bad)}. "
                f"valid: {sorted(VALID_DETECTORS)}"
            )

        severity = item.get("severity", "medium")
        if severity not in VALID_SEVERITIES:
            raise ConfigError(
                f"{where} ('{name}'): severity '{severity}' invalid. "
                f"valid: {sorted(VALID_SEVERITIES)}"
            )

        region = item.get("region", "custom")
        if region not in VALID_REGIONS:
            raise ConfigError(
                f"{where} ('{name}'): region '{region}' invalid. "
                f"valid: {sorted(VALID_REGIONS)}"
            )

        min_score = item.get("min_score")
        if min_score is not None:
            min_score = float(min_score)
            if not 0.0 <= min_score <= 1.0:
                raise ConfigError(
                    f"{where} ('{name}'): min_score {min_score} must be between 0 and 1"
                )

        entities[name] = Entity(
            name=name,
            description=description,
            min_score=min_score,
            detect_with=list(detect_with),
            severity=severity,
            replacement=item.get("replacement"),
            region=region,
        )
    return entities


def _load_patterns(raw: dict, entities: dict, check_completeness: bool = True) -> list:
    patterns = []
    for entity_name, items in raw.items():
        if entity_name not in entities:
            raise ConfigError(
                f"patterns.yaml: '{entity_name}' is not in taxonomy.yaml. "
                "Define the entity before writing patterns for it."
            )
        if not isinstance(items, list):
            raise ConfigError(f"patterns.yaml: '{entity_name}' must be a list of patterns")

        for i, item in enumerate(items):
            where = f"patterns.yaml {entity_name}[{i}]"
            regex = item.get("regex")
            if not regex:
                raise ConfigError(f"{where}: missing 'regex'")
            try:
                re.compile(regex)
            except re.error as exc:
                raise ConfigError(f"{where}: regex does not compile -- {exc}") from exc

            score = float(item.get("score", 0.8))
            if not 0.0 <= score <= 1.0:
                raise ConfigError(f"{where}: score {score} must be between 0 and 1")

            patterns.append(
                Pattern(
                    entity=entity_name,
                    name=item.get("name", f"{entity_name.lower()}_{i}"),
                    regex=regex,
                    score=score,
                )
            )

    if check_completeness:
        _check_every_regex_entity_has_a_pattern(entities, patterns)
    return patterns


def _check_every_regex_entity_has_a_pattern(entities: dict, patterns: list) -> None:
    for name, entity in entities.items():
        if entity.uses_regex and not any(p.entity == name for p in patterns):
            raise ConfigError(
                f"'{name}' declares detect_with: regex but has no pattern. "
                "It would silently never fire."
            )


def _resolve_entities(spec: list, inherited: list, context_name: str) -> list:
    """Apply a +add / -remove list against an inherited set, or replace it outright."""
    if not spec:
        return list(inherited)

    deltas = [s for s in spec if s.startswith(("+", "-"))]
    if deltas and len(deltas) != len(spec):
        raise ConfigError(
            f"rules.yaml context '{context_name}': mix of plain and +/- entries. "
            "Use all deltas or all absolutes, not both."
        )

    if not deltas:
        return list(spec)

    resolved = list(inherited)
    for item in spec:
        name = item[1:]
        if item.startswith("+"):
            if name not in resolved:
                resolved.append(name)
        elif name in resolved:
            resolved.remove(name)
    return resolved


def _load_contexts(raw: dict, entities: dict) -> dict:
    specs = raw.get("contexts")
    if not specs:
        raise ConfigError("rules.yaml defines no contexts")
    if "default" not in specs:
        raise ConfigError("rules.yaml must define a 'default' context")

    contexts = {}

    def build(name, seen):
        if name in contexts:
            return contexts[name]
        if name in seen:
            chain = " -> ".join(list(seen) + [name])
            raise ConfigError(f"rules.yaml: circular inherits: {chain}")
        if name not in specs:
            raise ConfigError(f"rules.yaml: context '{name}' is inherited but not defined")

        spec = specs[name]
        parent = None
        if spec.get("inherits"):
            parent = build(spec["inherits"], seen + [name])

        # `regions:` is the switchboard -- "India only", "medical only", "everything off
        # except secrets" -- without listing entity names. It seeds the entity set, then
        # `entities:` refines it with +/- deltas.
        regions = spec.get("regions")
        if regions is not None:
            if isinstance(regions, str):
                regions = [regions]
            bad = set(regions) - VALID_REGIONS
            if bad:
                raise ConfigError(
                    f"rules.yaml context '{name}': unknown regions {sorted(bad)}. "
                    f"valid: {sorted(VALID_REGIONS)}"
                )
            seed = [n for n, e in entities.items() if e.region in regions]
            if not seed:
                raise ConfigError(
                    f"rules.yaml context '{name}': regions {regions} match no entities in "
                    "taxonomy.yaml. It would redact nothing."
                )
        else:
            seed = parent.entities if parent else []

        entity_list = _resolve_entities(spec.get("entities", []), seed, name)
        if not entity_list:
            raise ConfigError(
                f"rules.yaml context '{name}': no entities. It would redact nothing."
            )

        unknown = [e for e in entity_list if e not in entities]
        if unknown:
            raise ConfigError(
                f"rules.yaml context '{name}': entities not in taxonomy.yaml: {unknown}"
            )

        threshold = float(
            spec.get("threshold", parent.threshold if parent else 0.4)
        )
        if not 0.0 <= threshold <= 1.0:
            raise ConfigError(
                f"rules.yaml context '{name}': threshold {threshold} must be 0..1"
            )

        strategy = spec.get("strategy", parent.strategy if parent else "consistent")
        if strategy not in VALID_STRATEGIES:
            raise ConfigError(
                f"rules.yaml context '{name}': strategy '{strategy}' invalid. "
                f"valid: {sorted(VALID_STRATEGIES)}"
            )

        ctx = Context(name, entity_list, threshold, strategy)
        contexts[name] = ctx
        return ctx

    for name in specs:
        build(name, [])
    return contexts


def load(config_dir=None) -> Config:
    d = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    entities = _load_entities(_read(d / "taxonomy.yaml"))
    patterns = _load_patterns(_read(d / "patterns.yaml"), entities)
    contexts = _load_contexts(_read(d / "rules.yaml"), entities)
    return Config(entities=entities, patterns=patterns, contexts=contexts)


def from_dicts(taxonomy: dict, patterns: dict = None, rules: dict = None) -> Config:
    """Build a Config from in-memory dicts instead of files.

    Same structure as the YAML, same validation. This is what a host product calls when it
    wants to declare its own sensitive data at runtime rather than shipping our config.
    """
    entities = _load_entities(taxonomy)
    pattern_list = _load_patterns(patterns or {}, entities)
    context_map = _load_contexts(rules or _implicit_rules(entities), entities)
    return Config(entities=entities, patterns=pattern_list, contexts=context_map)


def _implicit_rules(entities: dict) -> dict:
    """A caller who declares entities but no rules means 'redact all of them'."""
    return {
        "contexts": {
            "default": {
                "entities": list(entities),
                "threshold": 0.3,
                "strategy": "consistent",
            }
        }
    }


def extend(base: Config, taxonomy: dict, patterns: dict = None,
           contexts: dict = None) -> Config:
    """Return a NEW config: `base` plus caller-supplied entities, patterns and contexts.

    This is the capability that makes the component reusable by a host product. A support
    tool integrating hold-my-data does not edit our files -- it says "here is what MY
    customers' sensitive data looks like" at runtime, and gets a redactor that knows both
    our baseline entities and its own.

    `base` is never mutated: two products in one process must not be able to see, or
    corrupt, each other's taxonomy.

    Redefining an existing entity name is rejected. Silently overriding what a security
    control means is exactly the bug you would never find.
    """
    merged_entities = dict(base.entities)
    new_entities = _load_entities(taxonomy)

    clashes = sorted(set(new_entities) & set(base.entities))
    if clashes:
        raise ConfigError(
            f"cannot redefine existing entities: {clashes}. Pick different names -- "
            "silently changing what an entity means would be undetectable downstream."
        )
    merged_entities.update(new_entities)

    # completeness is checked once against the MERGED set -- the caller supplies patterns
    # only for its own entities, not for ours
    merged_patterns = list(base.patterns)
    merged_patterns.extend(
        _load_patterns(patterns or {}, merged_entities, check_completeness=False)
    )
    _check_every_regex_entity_has_a_pattern(merged_entities, merged_patterns)

    # re-validate the base contexts against the widened entity set, then add the new ones
    all_contexts = {
        name: {
            "entities": ctx.entities,
            "threshold": ctx.threshold,
            "strategy": ctx.strategy,
        }
        for name, ctx in base.contexts.items()
    }
    all_contexts.update(contexts or {})
    merged_contexts = _load_contexts({"contexts": all_contexts}, merged_entities)

    return Config(
        entities=merged_entities, patterns=merged_patterns, contexts=merged_contexts
    )
