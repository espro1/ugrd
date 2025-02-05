from typing import Union
from pathlib import Path
from subprocess import run, CompletedProcess, TimeoutExpired

from zenlib.util import pretty_print

__version__ = "1.3.3"
__author__ = "desultory"


def get_subpath(path: Path, subpath: Union[Path, str]) -> Path:
    """ Returns the subpath of a path. """
    if not isinstance(subpath, Path):
        subpath = Path(subpath)

    if subpath.is_absolute():
        return path / subpath.relative_to('/')
    else:
        return path / subpath


class GeneratorHelpers:
    """ Mixin class for the InitramfsGenerator class. """
    def _get_build_path(self, path: Union[Path, str]) -> Path:
        """ Returns the path relative to the build directory, under the tmpdir. """
        return get_subpath(get_subpath(self.tmpdir, self.build_dir), path)

    def _mkdir(self, path: Path, resolve_build=True) -> None:
        """
        Creates a directory within the build directory.
        If resolve_build is True, the path is resolved to the build directory.
        If not, the provided path is used as-is.
        """
        from os.path import isdir
        from os import mkdir
        if resolve_build:
            path = self._get_build_path(path)

        self.logger.log(5, "Creating directory: %s" % path)
        if path.is_dir():
            path_dir = path.parent
            self.logger.debug("Directory path: %s" % path_dir)
        else:
            path_dir = path

        if not isdir(path_dir.parent):
            self.logger.debug("Parent directory does not exist: %s" % path_dir.parent)
            self._mkdir(path_dir.parent, resolve_build=False)

        if not isdir(path_dir):
            mkdir(path)
            self.logger.log(self['_build_log_level'], "Created directory: %s" % path)
        else:
            self.logger.debug("Directory already exists: %s" % path_dir)

    def _write(self, file_name: Union[Path, str], contents: list[str], chmod_mask=0o644) -> None:
        """
        Writes a file within the build directory.
        Sets the passed chmod_mask.
        If the first line is a shebang, bash -n is run on the file.
        """
        from os import chmod
        file_path = self._get_build_path(file_name)

        if not file_path.parent.is_dir():
            self.logger.debug("Parent directory for '%s' does not exist: %s" % (file_path.name, file_path))
            self._mkdir(file_path.parent, resolve_build=False)

        if file_path.is_file():
            self.logger.warning("File already exists: %s" % file_path)
            if self.clean:
                self.logger.warning("Deleting file: %s" % file_path)
                file_path.unlink()

        self.logger.debug("[%s] Writing contents:\n%s" % (file_path, contents))
        with open(file_path, 'w') as file:
            file.writelines("\n".join(contents))

        if contents[0] == self.shebang:
            self.logger.debug("Running bash -n on file: %s" % file_name)
            try:
                self._run(['bash', '-n', str(file_path)])
            except RuntimeError as e:
                raise RuntimeError("Failed to validate bash script: %s" % pretty_print(contents)) from e

        self.logger.info("Wrote file: %s" % file_path)
        chmod(file_path, chmod_mask)
        self.logger.debug("[%s] Set file permissions: %s" % (file_path, chmod_mask))

    def _copy(self, source: Union[Path, str], dest=None) -> None:
        """ Copies a file into the initramfs build directory. """
        from shutil import copy2

        if not isinstance(source, Path):
            source = Path(source)

        if not dest:
            self.logger.log(5, "No destination specified, using source: %s" % source)
            dest = source

        dest_path = self._get_build_path(dest)

        if not dest_path.parent.is_dir():
            self.logger.debug("Parent directory for '%s' does not exist: %s" % (dest_path.name, dest_path.parent))
            self._mkdir(dest_path.parent, resolve_build=False)

        if dest_path.is_file():
            self.logger.warning("File already exists: %s" % dest_path)
        elif dest_path.is_dir():
            self.logger.debug("Destination is a directory, adding source filename: %s" % source.name)
            dest_path = dest_path / source.name

        self.logger.log(self['_build_log_level'], "Copying '%s' to '%s'" % (source, dest_path))
        copy2(source, dest_path)

    def _symlink(self, source: Union[Path, str], target: Union[Path, str]) -> None:
        """ Creates a symlink """
        from os import symlink

        if not isinstance(source, Path):
            source = Path(source)

        target = self._get_build_path(target)

        if not target.parent.is_dir():
            self.logger.debug("Parent directory for '%s' does not exist: %s" % (target.name, target.parent))
            self._mkdir(target.parent, resolve_build=False)

        if target.is_symlink():
            if target.resolve() == source:
                return self.logger.debug("Symlink already exists: %s -> %s" % (target, source))
            elif self.clean:
                self.logger.warning("Deleting symlink: %s" % target)
                target.unlink()
            else:
                raise RuntimeError("Symlink already exists: %s -> %s" % (target, target.resolve()))

        self.logger.debug("Creating symlink: %s -> %s" % (target, source))
        symlink(source, target)

    def _run(self, args: list[str], timeout=15) -> CompletedProcess:
        """ Runs a command, returns the CompletedProcess object """
        cmd_args = [str(arg) for arg in args]
        self.logger.debug("Running command: %s" % ' '.join(cmd_args))
        try:
            cmd = run(cmd_args, capture_output=True, timeout=timeout)
        except TimeoutExpired as e:
            raise RuntimeError("[%ds] Command timed out: %s" % (timeout, [str(arg) for arg in cmd_args])) from e

        if cmd.returncode != 0:
            self.logger.error("Failed to run command: %s" % ' '.join(cmd.args))
            self.logger.error("Command output:\n%s" % cmd.stdout.decode())
            self.logger.error("Command error:\n%s" % cmd.stderr.decode())
            raise RuntimeError("Failed to run command: %s" % ' '.join(cmd.args))

        return cmd

    def _rotate_old(self, file_name: Path, sequence=0) -> None:
        """ Copies a file to file_name.old then file_nane.old.n, where n is the next number in the sequence """
        # Nothing to do if the file doesn't exist
        if not file_name.is_file():
            self.logger.debug("File does not exist: %s" % file_name)
            return

        # If the cycle count is not set, attempt to clean
        if not self.old_count:
            if self.clean:
                self.logger.warning("Deleting file: %s" % file_name)
                file_name.unlink()
                return
            else:
                # Fail if the cycle count is not set and clean is disabled
                raise RuntimeError("Unable to cycle file, as cycle count is not set and clean is disabled: %s" % file_name)

        self.logger.debug("[%d] Cycling file: %s" % (sequence, file_name))

        # If the sequence is 0, we're cycling the file for the first time, just rename it to .old
        suffix = '.old' if sequence == 0 else '.old.%d' % sequence
        target_file = file_name.with_suffix(suffix)

        self.logger.debug("[%d] Target file: %s" % (sequence, target_file))
        # If the target file exists, cycle again
        if target_file.is_file():
            # First check if we've reached the cycle limit
            if sequence >= self.old_count:
                # Clean the last file in the sequence if clean is enabled
                if self.clean:
                    self.logger.warning("Deleting old file: %s" % target_file)
                    target_file.unlink()
                else:
                    self.logger.debug("Cycle limit reached")
                    return
            else:
                self.logger.debug("[%d] Target file exists, cycling again" % sequence)
                self._rotate_old(target_file, sequence + 1)

        # Finally, rename the file
        self.logger.info("[%d] Cycling file: %s -> %s" % (sequence, file_name, target_file))
        file_name.rename(target_file)
