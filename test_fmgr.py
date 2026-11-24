"""Tests unitaires de FileManager (lancer avec : python -m unittest -v)

FileManager ne dépend que d'interfaces : on lui injecte des doublures
(fakes) en mémoire, aucun fichier réel n'est touché.
"""
import unittest

from fmgr import (FileManager, SelectionProvider, FileSystemReader,
                  FileSystemWriter, UserInterface, ErrorChoice)


class FakeSelection(SelectionProvider):
    def __init__(self, files):
        self.files = list(files)

    def get_selected_files(self):
        return self.files

    def clear_selection(self):
        self.files = []


class FakeFileSystem(FileSystemReader, FileSystemWriter):
    """Système de fichiers en mémoire : chemin -> 'file' | 'dir'"""

    def __init__(self, entries, failing=()):
        self.entries = dict(entries)
        self.failing = set(failing)  # chemins qui provoquent une erreur E/S
        self.calls = []

    def _fail_if_needed(self, path):
        if path in self.failing:
            raise PermissionError(f"Locked: {path}")

    def _tree(self, path):
        return [p for p in self.entries if p == path or p.startswith(path + '/')]

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
        self._fail_if_needed(source)
        if source not in self.entries:
            raise FileNotFoundError(source)
        target = self._target(source, destination)
        self.entries[target] = self.entries[source]
        return target

    def move(self, source, destination):
        self.calls.append(('move', source, destination))
        self._fail_if_needed(source)
        if source not in self.entries:
            raise FileNotFoundError(source)
        target = self._target(source, destination)
        for p in self._tree(source):
            self.entries[target + p[len(source):]] = self.entries.pop(p)
        return target

    def remove_file(self, path):
        self.calls.append(('remove_file', path))
        del self.entries[path]

    def remove_tree(self, path):
        self.calls.append(('remove_tree', path))
        for p in self._tree(path):
            del self.entries[p]

    def make_temp_dir(self):
        self.entries['/trash'] = 'dir'
        return '/trash'


class FakeUI(UserInterface):
    """Interface utilisateur scriptée : renvoie les choix prévus, dans l'ordre"""

    def __init__(self, choices=()):
        self.choices = list(choices)
        self.messages = []
        self.errors = []

    def show_message(self, message):
        self.messages.append(message)

    def error_choice(self, operation, path, error):
        self.errors.append((operation, path, type(error).__name__))
        return self.choices.pop(0)


class FileManagerTestCase(unittest.TestCase):
    def make(self, entries, selected, failing=(), choices=()):
        self.fs = FakeFileSystem(entries, failing)
        self.selection = FakeSelection(selected)
        self.ui = FakeUI(choices)
        self.manager = FileManager(self.selection, self.fs, self.fs, self.ui)


# ---------------------------------------------------------------------------
# Non-régression des fonctionnalités existantes
# ---------------------------------------------------------------------------

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
        self.assertIn("1 file(s) copied", self.ui.messages)

    def test_copy_missing_file_asks_user(self):
        self.make({'/a/x.txt': 'file', '/b': 'dir'}, ['/a/missing.txt', '/a/x.txt'],
                  choices=[ErrorChoice.IGNORE])
        self.manager.copy_files('/b')
        self.assertEqual(self.ui.errors, [('Copy', '/a/missing.txt', 'FileNotFoundError')])
        self.assertTrue(self.fs.exists('/b/x.txt'))
        self.assertNotIn(('copy', '/a/missing.txt', '/b'), self.fs.calls)


class TestMove(FileManagerTestCase):
    def test_move_selected_files(self):
        self.make({'/a/x.txt': 'file', '/b': 'dir'}, ['/a/x.txt'])
        self.manager.move_files('/b')
        self.assertTrue(self.fs.exists('/b/x.txt'))
        self.assertFalse(self.fs.exists('/a/x.txt'))
        self.assertIn("1 file(s) moved", self.ui.messages)
        self.assertEqual(self.selection.get_selected_files(), [])


class TestDelete(FileManagerTestCase):
    def test_delete_file_and_directory(self):
        self.make({'/a/x.txt': 'file', '/a/d': 'dir', '/a/d/z.txt': 'file'},
                  ['/a/x.txt', '/a/d'])
        self.manager.delete_files()
        self.assertEqual(self.fs.entries, {})
        self.assertIn("2 file(s)/folder(s) deleted", self.ui.messages)

    def test_no_temp_trash_left_after_delete(self):
        self.make({'/a/x.txt': 'file'}, ['/a/x.txt'])
        self.manager.delete_files()
        self.assertFalse(self.fs.exists('/trash'))

    def test_delete_with_empty_selection(self):
        self.make({'/a/x.txt': 'file'}, [])
        self.manager.delete_files()
        self.assertEqual(self.fs.calls, [])
        self.assertTrue(self.fs.exists('/a/x.txt'))
        self.assertIn("0 file(s)/folder(s) deleted", self.ui.messages)


# ---------------------------------------------------------------------------
# Étape 3 : gestion des erreurs via UserInterface.error_choice
# ---------------------------------------------------------------------------

class TestErrorChoice(FileManagerTestCase):
    FILES = {'/a/1.txt': 'file', '/a/2.txt': 'file', '/a/3.txt': 'file',
             '/a/4.txt': 'file', '/b': 'dir'}
    SELECTED = ['/a/1.txt', '/a/2.txt', '/a/3.txt', '/a/4.txt']

    def test_no_error_does_not_ask_user(self):
        self.make(self.FILES, self.SELECTED)
        self.manager.copy_files('/b')
        self.assertEqual(self.ui.errors, [])
        self.assertIn("4 file(s) copied", self.ui.messages)

    def test_ignore_continues_and_asks_again_next_error(self):
        self.make(self.FILES, self.SELECTED, failing={'/a/2.txt', '/a/3.txt'},
                  choices=[ErrorChoice.IGNORE, ErrorChoice.IGNORE])
        self.manager.copy_files('/b')
        self.assertEqual(len(self.ui.errors), 2)
        self.assertTrue(self.fs.exists('/b/1.txt'))
        self.assertTrue(self.fs.exists('/b/4.txt'))
        self.assertIn("2 file(s) copied", self.ui.messages)

    def test_always_ignore_does_not_ask_again(self):
        self.make(self.FILES, self.SELECTED, failing={'/a/2.txt', '/a/3.txt'},
                  choices=[ErrorChoice.ALWAYS_IGNORE])
        self.manager.copy_files('/b')
        self.assertEqual(len(self.ui.errors), 1)
        self.assertTrue(self.fs.exists('/b/4.txt'))
        self.assertIn("2 file(s) copied", self.ui.messages)

    def test_always_ignore_only_lasts_for_current_operation(self):
        self.make(self.FILES, ['/a/2.txt'], failing={'/a/2.txt'},
                  choices=[ErrorChoice.ALWAYS_IGNORE, ErrorChoice.IGNORE])
        self.manager.copy_files('/b')
        self.selection.files = ['/a/2.txt']
        self.manager.copy_files('/b')
        self.assertEqual(len(self.ui.errors), 2)

    def test_stop_keeps_done_operations(self):
        self.make(self.FILES, self.SELECTED, failing={'/a/3.txt'},
                  choices=[ErrorChoice.STOP])
        self.manager.move_files('/b')
        self.assertTrue(self.fs.exists('/b/1.txt'))
        self.assertTrue(self.fs.exists('/b/2.txt'))
        self.assertTrue(self.fs.exists('/a/3.txt'))
        self.assertTrue(self.fs.exists('/a/4.txt'))  # jamais traité
        self.assertIn("Move stopped", self.ui.messages)
        self.assertIn("2 file(s) moved", self.ui.messages)

    def test_cancel_rolls_back_copy(self):
        self.make(self.FILES, self.SELECTED, failing={'/a/3.txt'},
                  choices=[ErrorChoice.CANCEL])
        self.manager.copy_files('/b')
        self.assertEqual(self.fs.entries, self.FILES)
        self.assertEqual(self.selection.get_selected_files(), self.SELECTED)

    def test_cancel_rolls_back_move(self):
        self.make(self.FILES, self.SELECTED, failing={'/a/3.txt'},
                  choices=[ErrorChoice.CANCEL])
        self.manager.move_files('/b')
        self.assertEqual(self.fs.entries, self.FILES)

    def test_cancel_rolls_back_delete(self):
        files = dict(self.FILES, **{'/a/d': 'dir', '/a/d/z.txt': 'file'})
        self.make(files, ['/a/d', '/a/1.txt', '/a/3.txt'], failing={'/a/3.txt'},
                  choices=[ErrorChoice.CANCEL])
        self.manager.delete_files()
        self.assertEqual(self.fs.entries, files)  # tout restauré, corbeille vidée

    def test_rollback_error_is_reported(self):
        self.make(self.FILES, self.SELECTED, failing={'/a/3.txt'},
                  choices=[ErrorChoice.CANCEL])
        original_move = self.fs.move

        def move_failing_on_rollback(source, destination):
            if source.startswith('/b/'):
                raise OSError("disk error")
            return original_move(source, destination)

        self.fs.move = move_failing_on_rollback
        self.manager.move_files('/b')
        self.assertTrue(any(m.startswith("Rollback error") for m in self.ui.messages))


if __name__ == '__main__':
    unittest.main()
