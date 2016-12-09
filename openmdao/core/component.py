"""Define the Component class."""

from __future__ import division

import collections

import numpy
from six import string_types

from openmdao.core.system import System


class Component(System):
    """Base Component class; not to be directly instantiated."""

    INPUT_DEFAULTS = {
        'shape': (1,),
        'units': '',
        'var_set': 0,
        'indices': [0],
    }

    OUTPUT_DEFAULTS = {
        'shape': (1,),
        'units': '',
        'var_set': 0,
        'lower': None,
        'upper': None,
        'ref': 1.0,
        'ref0': 0.0,
        'res_units': '',
        'res_ref': 1.0,
        'res_ref0': 0.0,
    }

    def add_input(self, name, val=1.0, **kwargs):
        """Add an input variable to the component.

        Args
        ----
        name : str60
            name of the variable in this component's namespace.
        val : object
            The value of the variable being added.
        **kwargs : dict
            additional args, documented [INSERT REF].
        """
        metadata = self.INPUT_DEFAULTS.copy()
        metadata.update(kwargs)

        if isinstance(val, numpy.ndarray) and 'indices' not in kwargs:
            metadata['indices'] = numpy.arange(0, val.size, dtype=int)
        else:
            metadata['indices'] = numpy.array(metadata['indices'])

        metadata['value'] = val
        if isinstance(val, numpy.ndarray):
            metadata['shape'] = val.shape

        self._variable_allprocs_names['input'].append(name)
        self._variable_myproc_names['input'].append(name)
        self._variable_myproc_metadata['input'].append(metadata)

    def add_output(self, name, val=1.0, **kwargs):
        """Add an output variable to the component.

        Args
        ----
        name : str
            name of the variable in this component's namespace.
        val : object
            The value of the variable being added.
        **kwargs : dict
            additional args, documented [INSERT REF].
        """
        metadata = self.OUTPUT_DEFAULTS.copy()
        metadata.update(kwargs)

        metadata['value'] = val
        if isinstance(val, numpy.ndarray):
            metadata['shape'] = val.shape

        self._variable_allprocs_names['output'].append(name)
        self._variable_myproc_names['output'].append(name)
        self._variable_myproc_metadata['output'].append(metadata)

    def _setup_vector(self, vectors, vector_var_ids, use_ref_vector):
        """See openmdao.core.component.Component._setup_vector."""
        super(Component, self)._setup_vector(vectors, vector_var_ids,
                                             use_ref_vector)

        # Components must load their initial input and output values into the
        # vectors.
        if vectors['input']._name is None:
            names = self._variable_myproc_names['input']
            inputs = self._inputs
            for i, meta in enumerate(self._variable_myproc_metadata['input']):
                inputs[names[i]] = meta['value']

        if vectors['output']._name is None:
            names = self._variable_myproc_names['output']
            outputs = self._outputs
            for i, meta in enumerate(self._variable_myproc_metadata['output']):
                outputs[names[i]] = meta['value']
