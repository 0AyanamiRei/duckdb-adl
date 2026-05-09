
Repository for NeuSO: Neural Optimizer for Subgraph Queries.


## Data Format
Data graph must have the following format:
```text
u{or 'd'} node_num edge_num v_label_num e_label_num
v v_id v_label
...
e src_id dst_id e_label
...
```
The first line, `u` represents undirected graph, while `d` represents directed graph. If the data graph is not a edge-labeled graph, then please set `e_label_num = 1` and set all `e_label = 0`.

Query graph file format:
```text
q node_num edge_num
v v_id v_label
...
e src_id dst_id e_label
...
```

The filtered graph file format:
```text
f node_num edge_num
v v_id C(v_id)
...
e src_id dst_id C(src_id, dst_id)
...
```

CCG file have the following format:
```text
t{or 'p'} node_num edge_num
v v_id v_card v_min_cost v_v_num v_1 v_2 ... v_v_num
...
w src_v_id dst_v_id join_v_id w_cost
```
The first line, `t` represents total explored, while `p` represents partially explored. Also, the ccg node_id must start from 0, continuous.

**Note** The v_id of graph should be started from 0 (not 1).

All these files are organized as the following directory structure:
```
- dataset/
    - dataset_name/
        - data_graph/
            - dataset_name.graph
        - query_graph/
            - query_graph_1.graph
            - ...
        - filter/
            - query_graph_1.filter
            - ...
        - ccg/
            - query_graph_1.ccg
            - ...
```


## How to Exp
* Modify the `config.yaml`.
* Run `python train.py` to train the model.


## Thanks

Thanks for the following repositories:
- [pytorch-template](https://github.com/victoresque/pytorch-template) for the template of PyTorch project.
- [pytorch-GAT](https://github.com/gordicaleksa/pytorch-GAT/tree/main) for the implementation of GAT which is the base of TriAT.
