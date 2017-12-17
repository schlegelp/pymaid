#    This script is part of pymaid (http://www.github.com/schlegelp/pymaid).
#    Copyright (C) 2017 Philipp Schlegel
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along

""" Collection of tools to turn CATMAID neurons into Graph representations.
"""

import sys
import math
import pylab
import numpy as np
import scipy.cluster
import logging
import pandas as pd
import time
from pymaid import core, fetch

import networkx as nx 

try:
    import igraph
except:
    igraph = None

# Set up logging
module_logger = logging.getLogger(__name__)
module_logger.setLevel(logging.DEBUG)
if len( module_logger.handlers ) == 0:
    # Generate stream handler
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    # Create formatter and add it to the handlers
    formatter = logging.Formatter(
                '%(levelname)-5s : %(message)s (%(name)s)')
    sh.setFormatter(formatter)
    module_logger.addHandler(sh)

__all__ = sorted(['network2graph','neuron2igraph','matrix2graph','cluster_nodes_w_synapses','dist_from_root','neuron2nx'])


def network2graph(x, remote_instance=None, threshold=1):
    """ Generates igraph network object

    Parameters
    ----------
    x                  
                        Catmaid Neurons as single or list of either:
                         1. skeleton IDs (int or str)
                         2. neuron name (str, exact match)
                         3. annotation: e.g. 'annotation:PN right'
                         4. CatmaidNeuron or CatmaidNeuronList object
                         5. Adjacency matrix (pd.DataFrame, rows=sources,
                            columns=targets)
    remote_instance :   CATMAID instance, optional 
                        Either pass directly to function or define globally 
                        as 'remote_instance'
    threshold :         int, optional
                        Connections weaker than this will be excluded 

    Returns
    ------- 
    iGraph representation of the network 

    Examples
    --------
    >>> import pymaid
    >>> import igraph
    >>> g = pymaid.network2graph('annotation:large network', remote_instance=rm)
    >>> # Plot graph
    >>> igraph.plot(g)
    >>> # Plot with edge width
    >>> igraph.plot(g, **{'edge_width': [ w/10 for w in g.es['weight'] ] })
    >>> # Plot with edge label
    >>> igraph.plot(g, **{'edge_label': g.es['weight'] })
    >>> # Save as graphml to import into e.g. Cytoscape
    >>> g.save('graph.graphml')
    """

    if remote_instance is None:
        if 'remote_instance' in sys.modules:
            remote_instance = sys.modules['remote_instance']
        elif 'remote_instance' in globals():
            remote_instance = globals()['remote_instance']
        else:
            print(
                'Please either pass a CATMAID instance or define globally as "remote_instance" ')
            return    

    if not isinstance(x, pd.DataFrame):
        module_logger.info('Generating network from skeleton IDs...')
        skids = fetch.eval_skids(x, remote_instance=remote_instance)
        indices = { int(s): skids.index(s) for s in skids}

        try:
            neuron_names = x.neuron_names.tolist()
        except:
            names = fetch.get_names(skids, remote_instance=remote_instance)
            neuron_names = [names[str(n)] for n in skids]

        edges = fetch.get_edges(skids, remote_instance=remote_instance)
        edges_by_index = [[indices[e.source_skid], indices[e.target_skid]]
                          for e in edges[edges.weight >= threshold].itertuples()]
        weight = edges[ edges.weight >= threshold ].weight.tolist()
    else:
        module_logger.info('Generating network from adjacancy matrix...')
        skids = list(set( x.columns.tolist() + x.index.tolist() ))
        neuron_names = skids

        edges = [ [i,j] for i in x.index.tolist() for j in x.columns.tolist() if x.loc[i,j] >= threshold ]       
        edges_by_index = [ [ skids.index(e[0]), skids.index(e[1]) ] for e in edges ] 
        weight = [ x.loc[i,j] for i in range( x.shape[0] ) for j in range( x.shape[1]) if x.loc[i,j] >= threshold ]

    # Generate graph and assign custom properties
    g = igraph.Graph(directed=True)
    g.add_vertices(len(skids))
    g.add_edges(edges_by_index)

    g.vs['node_id'] = skids
    g.vs['neuron_name'] = g.vs['label'] = neuron_names
    g.es['weight'] = weight

    return g


def matrix2graph(adj_matrix, **kwargs):
    """ Takes an adjacency matrix and turns it into an iGraph object

    Parameters
    ----------
    adj_matrix :      pandas.Dataframe
                      Adjacency matrix - e.g. from :func:`pymaid.create_adjacency_matrix`   
    syn_threshold :   int, optional     
                      Edges with less connections will be ignored
    syn_cutoff :      int, optional
                      Edges with more connections will be maxed at syn_cutoff

    Returns
    -------
    iGraph object
                      Representation of network   

    Examples
    --------
    >>> import pymaid
    >>> from igraph import plot as gplot
    >>> remote_instance = pymaid.CatmaidInstance(   URL, 
    ...                                             HTTP_USER, 
    ...                                             HTTP_PW, 
    ...                                             TOKEN )
    >>> neurons = pymaid.get_skids_by_annotation( 'right_pns' ,remote_instance)
    >>> mat = pymaid.matrix2graph(neurons,neurons,remote_instance)
    >>> g = pymaid.matrix2graph ( mat )
    >>> # Use Fruchterman-Reingold algorithm for layout
    >>> layout = g.layout('fr')
    >>> gplot( g, layout = layout )
    """

    syn_threshold = kwargs.get('syn_threshold', 1)
    syn_cutoff = kwargs.get('syn_cutoff', None)

    cols = adj_matrix.columns.tolist()
    rows = adj_matrix.index.tolist()

    # Extract values
    v = adj_matrix.values
    # Set values < syn_threshold to 0 -> will not show up on v.nonzero()
    v[v < syn_threshold] = 0

    # Get unique neurons in adj matrix
    neurons = list(set(cols + rows))

    # Generate dict containing the indices of neurons
    neurons_index = {n: i for i, n in enumerate(neurons)}

    # nonzero(): First index is row, second is column

    #edges = [ ( neurons.index ( rows[ v.nonzero()[0][i] ] ), neurons.index( cols[ v.nonzero()[1][i] ] ) ) for i in range( len( v.nonzero()[0] ) ) if v[ v.nonzero()[0][i] ][ v.nonzero()[1][i] ] >= syn_threshold ]
    #weights = [ v[ v.nonzero()[0][i] ][ v.nonzero()[1][i] ] for i in range( len( v.nonzero()[0] ) ) if v[ v.nonzero()[0][i] ][ v.nonzero()[1][i] ] >= syn_threshold ]

    # Get list of edges as indices of the vertices in the graph
    edges = [(neurons_index[rows[r]], neurons_index[cols[c]])
             for r, c in zip(v.nonzero()[0], v.nonzero()[1])]
    weights = [v[r][c] for r, c in zip(v.nonzero()[0], v.nonzero()[1])]

    if syn_cutoff:
        weights = [min(e, syn_cutoff) for e in weights]

    g = igraph.Graph(directed=True)

    # Add vertices
    g.add_vertices(len(neurons))
    g.vs['label'] = neurons

    # Add edges
    g.add_edges(edges)
    g.es['weight'] = weights

    return g

def neuron2nx(x):
    """ Takes CATMAID single skeleton data and turns it into an NetworkX Graph

    Parameters
    ----------
    skdata :          {pandas.DataFrame, CatmaidNeuron}
                      Containing either a single or multiple neurons.
    append :          bool, optional
                      Unless False, graph is automatically appended to the 
                      dataframe.

    Returns
    -------
    NetworX Graph
                      Representation of the neuron 

    """ 
    # Collect nodes
    nodes = x.nodes.set_index('treenode_id')
    # Collect edges
    edges = x.nodes[~x.nodes.parent_id.isnull()][['treenode_id','parent_id']].values
    # Collect weight
    weights = np.sqrt( np.sum( (nodes.loc[ edges[:,0], ['x','y','z'] ].values.astype(int) 
                                - nodes.loc[edges[:,1], ['x','y','z'] ].values.astype(int) )**2, axis=1) ) 
    # Generate weight dictionary
    edge_dict = np.array( [ { 'weight' : w } for w in weights ] )
    # Add weights to dictionary
    edges = np.append( edges, edge_dict.reshape( len( edges ), 1 ), axis=1)
    # Create empty directed Graph
    g = nx.DiGraph()    
    # Add nodes (in case we have disconnected nodes)
    g.add_nodes_from( x.nodes.treenode_id.values )
    # Add edges
    g.add_edges_from( edges )

    return g


def neuron2igraph(skdata, append=True):
    """ Takes CATMAID single skeleton data and turns it into an iGraph object

    Parameters
    ----------
    skdata :          {pandas.DataFrame, CatmaidNeuron}
                      Containing either a single or multiple neurons.
    append :          bool, optional
                      Unless False, graph is automatically appended to the 
                      dataframe.

    Returns
    -------
    iGraph object
                      Representation of the neuron 

    """
    # If igraph is not installed return nothing
    if igraph == None:
        return None

    if isinstance(skdata, pd.DataFrame) or isinstance(skdata, core.CatmaidNeuronList):
        return [neuron2graph(skdata.loc[i]) for i in range(skdata.shape[0])]
    elif isinstance(skdata, pd.Series) or isinstance(skdata, core.CatmaidNeuron):
        df = skdata

    module_logger.debug('Generating graph from skeleton data...')

    # Make sure we have correctly numbered indices
    nodes = df.nodes.reset_index(drop=True)

    # Generate list of vertices -> this order is retained
    vlist = nodes.treenode_id.tolist()

    # Get list of edges as indices (needs to exclude root node)
    tn_index_with_parent = nodes[
        ~nodes.parent_id.isnull()].index.values
    parent_ids = nodes[~nodes.parent_id.isnull()].parent_id.values
    nodes['temp_index'] = nodes.index  # add temporary index column
    parent_index = nodes.set_index('treenode_id').loc[parent_ids,
        'temp_index'].values    

    # Generate list of edges based on index of vertices
    elist = list(zip(tn_index_with_parent, parent_index))

    # Save this as backup
    #elist = [ [ n.Index, vlist.index( n.parent_id )  ] for n in nodes.itertuples() if n.parent_id != None ]

    # Generate graph and assign custom properties
    g = igraph.Graph(elist, n=len(vlist), directed=True)

    g.vs['node_id'] = nodes.treenode_id.tolist()
    g.vs['parent_id'] = nodes.parent_id.tolist()
    #g.vs['X'] = nodes.x.tolist()
    #g.vs['Y'] = nodes.y.tolist()
    #g.vs['Z'] = nodes.z.tolist()

    # Find nodes with synapses and assign the custom property'has_synapse'    
    # This turned out to be really time consuming
    #g.vs['has_synapse'] = False
    #g.vs.select(node_id_in=df.connectors.treenode_id.tolist())['has_synapse'] = True    

    # Generate weights by calculating edge lengths = distance between nodes
    tn_coords = nodes.loc[[e[0] for e in elist], ['x', 'y', 'z']].values
    parent_coords = nodes.loc[[e[1] for e in elist], ['x', 'y', 'z']].values

    w = np.sqrt(np.sum((tn_coords - parent_coords) ** 2, axis=1).astype(float)).tolist()
    g.es['weight'] = w

    if append:
        df.igraph = g

    return g


def dist_from_root(data, synapses_only=False):
    """ Get geodesic distance to root in nano meter (nm) for all treenodes 

    Parameters
    ----------
    data :            {iGraph object, pandas.DataFrame, CatmaidNeuron}
                      Holds the skeleton data.
    synapses_only :   bool, optional
                      If True, only distances for nodes with synapses will be 
                      returned (only makes sense if input is a Graph).

    Returns
    -------     
    dict             
                      Only if ``data`` is a graph object. 
                      Format ``{node_id : distance_to_root }``

    pandas DataFrame 
                      Only if ``data`` is a pandas DataFrame:. With 
                      ``df.nodes.dist_to_root`` holding the distances to root. 

    """

    if isinstance(data, Graph):
        g = data
    elif isinstance(data, pd.DataFrame) or isinstance(data, core.CatmaidNeuronList):
        return [dist_from_root(data.loc[i]) for i in range(data.shape[0])]
    elif isinstance(data, pd.Series) or isinstance(data, core.CatmaidNeuron):
        g = data.igraph
        if g is None:
            g = neuron2igraph(data)
    else:
        raise Exception('Unexpected data type: %s' % str(type(data)))

    # Generate distance matrix.
    try:
        module_logger.info('Generating distance matrix for neuron %s #%s...' % (
            data.neuron_name, str(data.skeleton_id)))
    except:
        module_logger.info('Generating distance matrix for igraph...')

    distance_matrix = g.shortest_paths_dijkstra(mode='All', weights='weight')

    if synapses_only:
        nodes = [ (v.index, v['node_id']) 
                 for v in g.vs.select(_node_id_in=data.connectors.treenode_id )]
    else:
        nodes = [(v.index, v['node_id']) for v in g.vs]

    root = [v.index for v in g.vs if v['parent_id'] == None][0]

    distances_to_root = {}

    for n in nodes:
        distances_to_root[n[1]] = distance_matrix[n[0]][root]

    if isinstance(data, igraph.Graph):
        return distances_to_root
    else:
        data.nodes['dist_to_root'] = [distances_to_root[n]
                                      for n in data.nodes.treenode_id.tolist()]
        data.igraph = neuron2igraph(data)
        return data


def cluster_nodes_w_synapses(data, plot_graph=False):
    """ Cluster nodes of an iGraph object based on distance

    Parameters
    ----------
    data :         {CatmaidNeuron, iGraph object}
    plot_graph :   bool, optional
                   If True, plots a Graph. 

    Returns
    -------
    cluster :      linkage matrix
                   Scipy hiearchical clustering encoded as linkage matrix
    """

    if isinstance(data, igraph.Graph):
        g = data
    elif isinstance(data, pd.DataFrame) or isinstance(data, core.CatmaidNeuronList):
        if data.shape[0] == 1:
            g = data.loc[0].igraph
        else:
            raise Exception('Please provide a SINGLE neuron.')
    elif isinstance(data, pd.Series) or isinstance(data, core.CatmaidNeuron):
        g = data.igraph
        if g == None:
            g = neuron2igraph(data)
    else:
        raise Exception('Unexpected data type: %s' % str(type(data)))

    module_logger.info('Generating distance matrix for neuron...')
    # Generate distance matrix.
    distance_matrix = g.shortest_paths_dijkstra(mode='All', weights='weight')

    # List of nodes without synapses
    not_synapse_nodes = [v.index for v in g.vs.select(has_synapse=False)]

    # Delete non synapse nodes from distance matrix (columns first, then rows)
    distance_matrix_syn = np.delete(distance_matrix, not_synapse_nodes, 0)
    distance_matrix_syn = np.delete(distance_matrix_syn, not_synapse_nodes, 1)

    module_logger.info('Clustering nodes with synapses...')
    Y_syn = scipy.hierarchy.ward(distance_matrix_syn)

    if plot_graph:
        module_logger.debug('Plotting graph')
        # Compute and plot first dendrogram for all nodes.
        fig = pylab.figure(figsize=(8, 8))
        ax1 = fig.add_axes([0.09, 0.1, 0.2, 0.6])
        Y_all = scipy.hierarchy.ward(distance_matrix)
        Z1 = scipy.hierarchy.dendrogram(Y_all, orientation='left')
        ax1.set_xticks([])
        ax1.set_yticks([])

        # Compute and plot second dendrogram for synapse nodes only.
        ax2 = fig.add_axes([0.3, 0.71, 0.6, 0.2])
        Z2 = scipy.hierarchy.dendrogram(Y_syn)
        ax2.set_xticks([])
        ax2.set_yticks([])

        # Plot distance matrix.
        axmatrix = fig.add_axes([0.3, 0.1, 0.6, 0.6])
        idx1 = Z1['leaves']
        idx2 = Z2['leaves']
        D = np.delete(distance_matrix, not_synapse_nodes, 1)
        D = D[idx1, :]
        D = D[:, idx2]
        im = axmatrix.matshow(
            D, aspect='auto', origin='lower', cmap=pylab.cm.YlGnBu)
        axmatrix.set_xticks([])
        axmatrix.set_yticks([])

        # Plot colorbar.
        axcolor = fig.add_axes([0.91, 0.1, 0.02, 0.6])
        pylab.colorbar(im, cax=axcolor)
        fig.show()

    return Y_syn

def _find_all_paths(g, start, end, mode = 'OUT', maxlen = None):
    """ Find all paths between to vertices in an iGraph object. For some reason
    this function is only in R-iGraph, not Python-iGraph. This is rather slow
    and should not be used for large graphs.
    """
    def find_all_paths_aux(adjlist, start, end, path, maxlen = None):
        path = path + [start]
        if start == end:
            return [path]
        paths = []
        if maxlen is None or len(path) <= maxlen:
            for node in adjlist[start] - set(path):
                paths.extend(find_all_paths_aux(adjlist, node, end, path, maxlen))
        return paths
    adjlist = [set(g.neighbors(node, mode = mode)) \
        for node in range(g.vcount())]
    all_paths = []
    start = start if type(start) is list else [start]
    end = end if type(end) is list else [end]
    for s in start:
        for e in end:
            all_paths.extend(find_all_paths_aux(adjlist, s, e, [], maxlen))
    return all_paths