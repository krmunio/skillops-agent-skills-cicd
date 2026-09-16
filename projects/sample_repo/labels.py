"""Label normalization seed with realistic ordering and canonicalization bugs."""


def normalize_labels(labels):
    return sorted(set(label.lower() for label in labels))
