"""Tests unitaires de FileManager (lancer avec : python -m unittest -v)

FileManager ne dépend que d'interfaces : on lui injecte des doublures
(fakes) en mémoire, aucun fichier réel n'est touché.
"""
import unittest

from fmgr import (FileManager, SelectionProvider, FileSystemReader,
                  FileSystemWriter, MessageOutput)


class FakeSelection(SelectionProvider):
    def __init__(self, files):
        self.files = list(files)

    def get_selected_files(self):
        return self.files

    def clear_selection(self):
        self.files = []


class FakeFileSystem(FileSystemReader, FileSystemWriter):
    """Système de fichiers en mémoire : chemin -> 'file' | 'dir'"""

    def __init__(self, entries):
        self.entries = dict(entries)
        self.calls = []

    def exists(self, path):
        return path in self.entries

    def is_file(self, path):
        return self.entries.get(path) == 'file'

    def is_dir(self, path):
        return self.entries.get(path) == 'dir'

    def list_dir(self, path):
        prefix = path.rstrip('/') + '/'
        return [p[len(prefix):] for p in self.entries
                if p.startswith(prefix) and '/' not in p[len(prefix):]]

    @staticmethod
    def _target(source, destination):
        return destination.rstrip('/') + '/' + source.rsplit('/', 1)[-1]

    def copy(self, source, destination):
        self.calls.append(('copy', source, destination))
        if source not in self.entries:
            raise FileNotFoundError(source)
        target = self._target(source, destination)
        self.entries[target] = self.entries[source]
        return target

    def move(self, source, destination):
        self.calls.append(('move', source, destination))
        if source not in self.entries:
            raise FileNotFoundError(source)
        target = self._target(source, destination)
        self.entries[target] = self.entries.pop(source)
        return target

    def remove_file(self, path):
        self.calls.append(('remove_file', path))
        del self.entries[path]

    def remove_tree(self, path):
        self.calls.append(('remove_tree', path))
        for p in [p for p in self.entries if p == path or p.startswith(path + '/')]:
            del self.entries[p]


class FakeOutput(MessageOutput):
    def __init__(self):
        self.messages = []

    def show_message(self, message):
        self.messages.append(message)


class FileManagerTestCase(unittest.TestCase):
    def make(self, entries, selected):
        self.fs = FakeFileSystem(entries)
        self.selection = FakeSelection(selected)
        self.output = FakeOutput()
        self.manager = FileManager(self.selection, self.fs, self.fs, self.output)


class TestCopy(FileManagerTestCase):
    def test_copy_selected_files_to_destination(self):
        self.make({'/a/x.txt': 'file', '/a/y.txt': 'file', '/b': 'dir'},
                  ['/a/x.txt', '/a/y.txt'])
        self.manager.copy_files('/b')
        self.assertTrue(self.fs.exists('/b/x.txt'))
        self.assertTrue(self.fs.exists('/b/y.txt'))
        self.assertTrue(self.fs.exists('/a/x.txt'))  # la source reste

    def test_copy_clears_selection_and_reports(self):
        self.make({'/a/x.txt': 'file', '/b': 'dir'}, ['/a/x.txt'])
        self.manager.copy_files('/b')
        self.assertEqual(self.selection.get_selected_files(), [])
        self.assertIn("1 file(s) copied", self.output.messages)

    def test_copy_skips_missing_file(self):
        self.make({'/a/x.txt': 'file', '/b': 'dir'}, ['/a/missing.txt', '/a/x.txt'])
        self.manager.copy_files('/b')
        self.assertTrue(self.fs.exists('/b/x.txt'))
        self.assertNotIn(('copy', '/a/missing.txt', '/b'), self.fs.calls)


class TestMove(FileManagerTestCase):
    def test_move_selected_files(self):
        self.make({'/a/x.txt': 'file', '/b': 'dir'}, ['/a/x.txt'])
        self.manager.move_files('/b')
        self.assertTrue(self.fs.exists('/b/x.txt'))
        self.assertFalse(self.fs.exists('/a/x.txt'))
        self.assertIn("1 file(s) moved", self.output.messages)
        self.assertEqual(self.selection.get_selected_files(), [])


class TestDelete(FileManagerTestCase):
    def test_delete_file_and_directory(self):
        self.make({'/a/x.txt': 'file', '/a/d': 'dir', '/a/d/z.txt': 'file'},
                  ['/a/x.txt', '/a/d'])
        self.manager.delete_files()
        self.assertIn(('remove_file', '/a/x.txt'), self.fs.calls)
        self.assertIn(('remove_tree', '/a/d'), self.fs.calls)
        self.assertEqual(self.fs.entries, {})
        self.assertIn("2 file(s)/folder(s) deleted", self.output.messages)

    def test_delete_with_empty_selection(self):
        self.make({'/a/x.txt': 'file'}, [])
        self.manager.delete_files()
        self.assertEqual(self.fs.calls, [])
        self.assertTrue(self.fs.exists('/a/x.txt'))


if __name__ == '__main__':
    unittest.main()
