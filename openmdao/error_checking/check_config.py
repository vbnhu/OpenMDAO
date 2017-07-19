"""A module containing various configuration checks for an OpenMDAO Problem."""

import sys
import logging
from collections import defaultdict

import numpy as np

import networkx as nx
from six import iteritems

from openmdao.core.group import Group
from openmdao.core.component import Component

# when setup is called multiple times, we need this to prevent adding
# another handler to the config_check logger each time (if logger arg to check_config is None)
_set_logger = None


def check_config(problem, logger=None):
    """
    Perform optional error checks on a Problem.

    Parameters
    ----------
    problem : Problem
        The Problem being checked.

    logger : object
        Logging object.
    """
    global _set_logger
    if logger is None:
        if _set_logger is None:
            logger = logging.getLogger("config_check")
            _set_logger = logger
            console = logging.StreamHandler(sys.stdout)
            # set a format which is simpler for console use
            formatter = logging.Formatter('%(levelname)s: %(message)s')
            # tell the handler to use this format
            console.setFormatter(formatter)
            console.setLevel(logging.INFO)
            logger.addHandler(console)
        else:
            logger = _set_logger

    _check_hanging_inputs(problem, logger)

    for system in problem.model.system_iter(include_self=True, recurse=True):
        # system specific check
        system.check_config(logger)
        # check dataflow within Group
        if isinstance(system, Group):
            _check_dataflow(system, logger)


def compute_sys_graph(group, input_srcs, comps_only=False, save_vars=False):
    """
    Compute a dependency graph for subsystems in the given group.

    Parameters
    ----------
    group : <Group>
        The Group we're computing the graph for.

    input_srcs : {}
        dict containing global variable abs names for sources of the inputs.

    comps_only : bool (False)
        If True, return a graph of all Components within the given group
        or any of its descendants. No sub-groups will be included. Otherwise,
        a graph containing only direct children (both Components and Groups)
        of the group will be returned.

    save_vars : bool (False)
        If True, store var connection information in each edge in the system
        graph.

    Returns
    -------
    DiGraph
        A directed graph containing names of subsystems and their connections.
    """
    glen = len(group.pathname.split('.')) if group.pathname else 0
    graph = nx.DiGraph()

    if comps_only:
        subsystems = list(group.system_iter(recurse=True, typ=Component))
    else:
        subsystems = group._subsystems_allprocs

    if save_vars:
        edge_data = defaultdict(lambda: defaultdict(list))

    for in_abs, src_abs in iteritems(input_srcs):
        if src_abs is not None:
            iparts = in_abs.split('.')
            oparts = src_abs.split('.')
            if comps_only:
                src = '.'.join(oparts[glen:-1])
                tgt = '.'.join(iparts[glen:-1])
            else:
                src = oparts[glen]
                tgt = iparts[glen]

            if save_vars:
                # store var connection data in each system to system edge for later
                # use in relevance calculation.
                edge_data[(src, tgt)][src_abs].append(in_abs)
            else:
                graph.add_edge(src, tgt)

    if save_vars:
        for key in edge_data:
            src_sys, tgt_sys = key
            graph.add_edge(src_sys, tgt_sys, conns=edge_data[key])

    return graph


def get_sccs(group, comps_only=False):
    """
    Return strongly connected subsystems of the given Group.

    Parameters
    ----------
    group : <Group>
        The strongly connected components will be computed for this Group.

    comps_only : bool (False)
        If True, the graph used to compute strongly connected components
        will contain all Components within the given group or any of its
        descendants and no sub-groups will be included. Otherwise, the graph
        used will contain only direct children (both Components and Groups)
        of the given group.

    Returns
    -------
    list of sets of str
        A list of strongly connected components in topological order.
    """
    graph = compute_sys_graph(group, group._conn_global_abs_in2out,
                              comps_only=comps_only)

    # Tarjan's algorithm returns SCCs in reverse topological order, so
    # the list returned here is reversed.
    sccs = list(nx.strongly_connected_components(graph))
    sccs.reverse()
    return sccs


def all_connected_edges(graph, start):
    """

    Yield all downstream edges starting at the given node.

    Parameters
    ----------
    graph : network.DiGraph
        Graph being traversed.
    start : hashable object
        Identifier of the starting node.

    Yields
    ------
    list
        A list of all edges found when traversal starts at start.
    """
    visited = set()
    stack = [start]
    while stack:
        src = stack.pop()
        for tgt in graph[src]:
            yield src, tgt
            if tgt not in visited:
                visited.add(tgt)
                stack.append(tgt)


def get_relevant_vars(graph, desvars, responses):
    """
    Find all relevant vars between desvars and responses.

    Both vars are assumed to be outputs (either design vars or responses).

    Parameters
    ----------
    graph : networkx.DiGraph
        System graph with var connection info on the edges.
    desvars : list of str
        Names of design variables.
    responses : list of str
        Names of response variables.

    Returns
    -------
    dict
        Dict of (dep_outputs, dep_inputs, dep_systems) keyed by design vars and responses.
    """
    relevant = defaultdict(dict)
    edge_cache = {}

    grev = graph.reverse()

    for desvar in desvars:
        start_sys = desvar.rsplit('.', 1)[0]
        if start_sys not in edge_cache:
            edge_cache[start_sys] = set(all_connected_edges(graph, start_sys))
        start_edges = edge_cache[start_sys]

        for response in responses:
            end_sys = response.rsplit('.', 1)[0]
            if end_sys not in edge_cache:
                edge_cache[end_sys] = set((v, u) for u, v in
                                          all_connected_edges(grev, end_sys))
            end_edges = edge_cache[end_sys]

            common_edges = start_edges.intersection(end_edges)

            input_deps = set()
            output_deps = set([desvar, response])
            sys_deps = set()
            for u, v in common_edges:
                sys_deps.add(u)
                sys_deps.add(v)
                conns = graph[u][v]['conns']
                output_deps.update(conns)
                for inputs in conns.values():
                    input_deps.update(inputs)

            if sys_deps:
                relevant[desvar][response] = rel = (input_deps, output_deps, sys_deps)
                relevant[response][desvar] = rel

    # TODO: if we knew mode here, we would only need to compute for fwd or rev,
    # instead of both.

    # now calculate dependencies between each VOI and all other VOIs of the
    # other type, e.g for each input VOI wrt all output VOIs.
    for inputs, outputs in [(desvars, responses), (responses, desvars)]:
        for inp in inputs:
            if inp in relevant:
                relinp = relevant[inp]
                total_inps = set()
                total_outs = set()
                total_systems = set()
                for out in outputs:
                    if out in relinp:
                        inps, outs, systems = relinp[out]
                        total_inps.update(inps)
                        total_outs.update(outs)
                        total_systems.update(systems)
                relinp['@all'] = (total_inps, total_outs, total_systems)

    return relevant


def _check_dataflow(group, logger):
    """
    Report any cycles and out of order Systems to the logger.

    Parameters
    ----------
    group : <Group>
        The Group being checked for dataflow issues.

    logger : object
        The object that manages logging output.
    """
    sccs = get_sccs(group)
    cycles = [sorted(s) for s in sccs if len(s) > 1]
    cycle_idxs = {}

    if cycles:
        logger.warning("Group '%s' has the following cycles: %s" %
                       (group.pathname, cycles))
        for i, cycle in enumerate(cycles):
            # keep track of cycles so we can detect when a system in
            # one cycle is out of order with a system in a different cycle.
            for s in cycle:
                cycle_idxs[s] = i

    ubcs = _get_out_of_order_subs(group, group._conn_global_abs_in2out)

    for tgt_system, src_systems in sorted(ubcs.items()):
        keep_srcs = []

        for src_system in src_systems:
            if not (src_system in cycle_idxs and
                    tgt_system in cycle_idxs and
                    cycle_idxs[tgt_system] == cycle_idxs[src_system]):
                keep_srcs.append(src_system)

        if keep_srcs:
            logger.warning("System '%s' executes out-of-order with "
                           "respect to its source systems %s" %
                           (tgt_system, sorted(keep_srcs)))


def _get_out_of_order_subs(group, input_srcs):
    """
    Return Systems that are executed out of dataflow order.

    Parameters
    ----------
    group : <Group>
        The Group where we're checking subsystem order.

    input_srcs : {}
        dict containing variable abs names for sources of the inputs.
        This describes all variable connections, either explicit or implicit,
        in the entire model.

    Returns
    -------
    dict
        A dict mapping names of target Systems to a list of names of their
        source Systems that execute after them.
    """
    subsystems = group._subsystems_allprocs
    sub2i = {sub.name: i for i, sub in enumerate(subsystems)}
    glen = len(group.pathname.split('.')) if group.pathname else 0

    ubcs = defaultdict(list)
    for in_abs, src_abs in iteritems(input_srcs):
        if src_abs is not None:
            iparts = in_abs.split('.')
            oparts = src_abs.split('.')
            src_sys = oparts[glen]
            tgt_sys = iparts[glen]
            if (src_sys in sub2i and tgt_sys in sub2i and
                    (sub2i[src_sys] > sub2i[tgt_sys])):
                ubcs['.'.join(iparts[:glen + 1])].append('.'.join(oparts[:glen + 1]))

    return ubcs


def _check_hanging_inputs(problem, logger):
    """
    Issue a logger warning if any inputs are not connected.

    Parameters
    ----------
    problem : <Problem>
        The problem being checked.

    logger : object
        The object that managers logging output.
    """
    input_srcs = problem.model._conn_global_abs_in2out

    hanging = sorted([
        name
        for name in problem.model._var_allprocs_abs_names['input']
        if name not in input_srcs
    ])

    if hanging:
        logger.warning("The following inputs are not connected: %s." % hanging)
