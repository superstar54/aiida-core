###########################################################################
# Copyright (c), The AiiDA team. All rights reserved.                     #
# This file is part of the AiiDA code.                                    #
#                                                                         #
# The code is hosted on GitHub at https://github.com/aiidateam/aiida-core #
# For further information on the license, see the LICENSE.txt file        #
# For further information please visit http://www.aiida.net               #
###########################################################################
"""Data plugin representing an executable code stored in AiiDA's storage.

This plugin should be used for executables that are not already installed on the target computer, but instead are
available on the machine where AiiDA is running. The plugin assumes that the code is self-contained by a single
directory containing all the necessary files, including a main executable. When constructing a ``PortableCode``, passing
the absolute filepath as ``filepath_files`` will make sure that all the files contained within are uploaded to AiiDA's
storage. The ``filepath_executable`` should indicate the filename of the executable within that directory. Each time a
:class:`aiida.engine.CalcJob` is run using a ``PortableCode``, the uploaded files will be automatically copied to the
working directory on the selected computer and the executable will be run there.
"""

from __future__ import annotations

import contextlib
import logging
import pathlib
import typing as t

from pydantic import field_validator

from aiida.common import exceptions
from aiida.common.folders import Folder
from aiida.common.lang import type_check
from aiida.common.pydantic import MetadataField
from aiida.orm import Computer

from .abstract import AbstractCode
from .legacy import Code

__all__ = ('PortableCode',)
_LOGGER = logging.getLogger(__name__)


class PortableCode(Code):
    """Data plugin representing an executable code stored in AiiDA's storage."""

    _EMIT_CODE_DEPRECATION_WARNING: bool = False
    _KEY_ATTRIBUTE_FILEPATH_EXECUTABLE: str = 'filepath_executable'

    class Model(AbstractCode.Model):
        """Model describing required information to create an instance."""

        filepath_files: t.Union[str, pathlib.Path] = MetadataField(
            ...,
            title='Code directory',
            description='Filepath to directory containing code files.',
            short_name='-F',
            priority=2,
        )
        filepath_executable: str = MetadataField(
            ...,
            title='Filepath executable',
            description='Relative filepath of executable with directory of code files.',
            short_name='-X',
            priority=1,
        )

        @field_validator('filepath_files')
        @classmethod
        def validate_filepath_files(cls, value: str) -> pathlib.Path:
            """Validate that ``filepath_files`` is an existing directory."""
            filepath = pathlib.Path(value)
            if not filepath.exists():
                raise ValueError(f'The filepath `{value}` does not exist.')
            if not filepath.is_dir():
                raise ValueError(f'The filepath `{value}` is not a directory.')
            return filepath

    def __init__(self, filepath_executable: str, filepath_files: pathlib.Path | str, **kwargs):
        """Construct a new instance.

        .. note:: If the files necessary for this code are not all located in a single directory or the directory
            contains files that should not be uploaded, and so the ``filepath_files`` cannot be used. One can use the
            methods of the :class:`aiida.orm.nodes.repository.NodeRepository` class. This can be accessed through the
            ``base.repository`` attribute of the instance after it has been constructed. For example::

                code = PortableCode(filepath_executable='some_name.exe')
                code.put_object_from_file()
                code.put_object_from_filelike()
                code.put_object_from_tree()

        :param filepath_executable: The relative filepath of the executable within the directory of uploaded files.
        :param filepath_files: The filepath to the directory containing all the files of the code.
        """
        super().__init__(**kwargs)
        type_check(filepath_files, pathlib.Path)
        self.filepath_executable = filepath_executable  # type: ignore[assignment]
        self.base.repository.put_object_from_tree(str(filepath_files))

    def _validate(self):
        """Validate the instance by checking that an executable is defined and it is part of the repository files.

        :raises :class:`aiida.common.exceptions.ValidationError`: If the state of the node is invalid.
        """
        super(Code, self)._validate()  # Change to ``super()._validate()`` once deprecated ``Code`` class is removed.

        try:
            filepath_executable = self.filepath_executable
        except TypeError as exception:
            raise exceptions.ValidationError('The `filepath_executable` is not set.') from exception

        objects = self.base.repository.list_object_names()

        if str(filepath_executable) not in objects:
            raise exceptions.ValidationError(
                f'The executable `{filepath_executable}` is not one of the uploaded files: {objects}'
            )

    def can_run_on_computer(self, computer: Computer) -> bool:
        """Return whether the code can run on a given computer.

        A ``PortableCode`` should be able to be run on any computer in principle.

        :param computer: The computer.
        :return: ``True`` if the provided computer is the same as the one configured for this code.
        """
        return True

    def get_executable(self) -> pathlib.PurePath:
        """Return the executable that the submission script should execute to run the code.

        :return: The executable to be called in the submission script.
        """
        return self.filepath_executable

    def validate_working_directory(self, folder: Folder):
        """Validate content of the working directory created by the :class:`~aiida.engine.CalcJob` plugin.

        This method will be called by :meth:`~aiida.engine.processes.calcjobs.calcjob.CalcJob.presubmit` when a new
        calculation job is launched, passing the :class:`~aiida.common.folders.Folder` that was used by the plugin used
        for the calculation to create the input files for the working directory. This method can be overridden by
        implementations of the ``AbstractCode`` class that need to validate the contents of that folder.

        :param folder: A sandbox folder that the ``CalcJob`` plugin wrote input files to that will be copied to the
            working directory for the corresponding calculation job instance.
        :raises PluginInternalError: The ``CalcJob`` plugin created a file that has the same relative filepath as the
            executable for this portable code.
        """
        if str(self.filepath_executable) in folder.get_content_list():
            raise exceptions.PluginInternalError(
                f'The plugin created a file {self.filepath_executable} that is also the executable name!'
            )

    @property
    def full_label(self) -> str:
        """Return the full label of this code.

        The full label can be just the label itself but it can be something else. However, it at the very least has to
        include the label of the code.

        :return: The full label of the code.
        """
        return self.label

    @property
    def filepath_executable(self) -> pathlib.PurePath:
        """Return the relative filepath of the executable that this code represents.

        :return: The relative filepath of the executable.
        """
        return pathlib.PurePath(self.base.attributes.get(self._KEY_ATTRIBUTE_FILEPATH_EXECUTABLE))

    @filepath_executable.setter
    def filepath_executable(self, value: str) -> None:
        """Set the relative filepath of the executable that this code represents.

        :param value: The relative filepath of the executable within the directory of uploaded files.
        """
        type_check(value, str)

        if pathlib.PurePath(value).is_absolute():
            raise ValueError('The `filepath_executable` should not be absolute.')

        self.base.attributes.set(self._KEY_ATTRIBUTE_FILEPATH_EXECUTABLE, value)

    def _prepare_yaml(self, *args, **kwargs):
        """Export code to a YAML file."""
        try:
            target = pathlib.Path().cwd() / f'{self.label}'
            setattr(self, 'filepath_files', str(target))
            result = super()._prepare_yaml(*args, **kwargs)[0]

            extra_files = {}
            node_repository = self.base.repository

            # Logic taken from `copy_tree` method of the `Repository` class and adapted to return
            # the relative file paths and their utf-8 encoded content as `extra_files` dictionary
            path = '.'
            for root, dirnames, filenames in node_repository.walk():
                for filename in filenames:
                    rel_output_file_path = root.relative_to(path) / filename
                    full_output_file_path = target / rel_output_file_path
                    full_output_file_path.parent.mkdir(exist_ok=True, parents=True)

                    extra_files[str(full_output_file_path)] = node_repository.get_object_content(
                        str(rel_output_file_path), mode='rb'
                    )
            _LOGGER.warning(f'Repository files for PortableCode <{self.pk}> dumped to folder `{target}`.')

        finally:
            with contextlib.suppress(AttributeError):
                delattr(self, 'filepath_files')

        return result, extra_files
