"""Build Presidio PatternRecognizers from patterns.yaml.

Deterministic layer. Runs on every context, costs nothing, and catches the things that have
an exact shape -- keys, tokens, structured ids. Per the PRD this layer is built and scored
first; the model layer has to beat it to earn its place.
"""

from presidio_analyzer import Pattern as PresidioPattern
from presidio_analyzer import PatternRecognizer


def build(config, entity_names=None) -> list:
    """One PatternRecognizer per entity that declares regex detection."""
    recognizers = []
    for name, entity in config.entities.items():
        if entity_names is not None and name not in entity_names:
            continue
        if not entity.uses_regex:
            continue

        patterns = [
            PresidioPattern(name=p.name, regex=p.regex, score=p.score)
            for p in config.patterns_for(name)
        ]
        if not patterns:
            continue

        recognizers.append(
            PatternRecognizer(
                supported_entity=name,
                name=f"regex_{name.lower()}",
                patterns=patterns,
                supported_language="en",
            )
        )
    return recognizers
