import unittest

from enqueue import Item, enqueue_many


class MemoryBackend:
    def __init__(self):
        self.saved = []

    def save(self, item, status):
        self.saved.append((item, status))


class EnqueueTests(unittest.TestCase):
    def test_dependent_generator(self):
        items = [Item("a", ("parent",)), Item("b", ("parent",))]
        backend = MemoryBackend()
        self.assertEqual(enqueue_many(iter(items), backend), items)
        self.assertEqual(backend.saved, [(item, "deferred") for item in items])

    def test_empty_iterable(self):
        backend = MemoryBackend()
        self.assertEqual(enqueue_many(iter(()), backend), [])
        self.assertEqual(backend.saved, [])

    def test_generator_matches_list(self):
        items = [Item("child", ("parent",)), Item("free"), Item("child-2", ("parent",))]
        left, right = MemoryBackend(), MemoryBackend()
        self.assertEqual(enqueue_many(iter(items), left), enqueue_many(items, right))
        self.assertEqual(left.saved, right.saved)

    def test_input_is_unchanged(self):
        items = [Item("child", ("parent",)), Item("free")]
        before = list(items)
        enqueue_many(items, MemoryBackend())
        self.assertEqual(items, before)

    def test_stable_partition(self):
        items = [Item("a", ("x",)), Item("b"), Item("c", ("y",)), Item("d")]
        backend = MemoryBackend()
        self.assertEqual(enqueue_many(items, backend), [items[1], items[3], items[0], items[2]])
        self.assertEqual([status for _, status in backend.saved],
                         ["queued", "queued", "deferred", "deferred"])
