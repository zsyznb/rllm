# The BRIDGE method from the "rLLM: Relational Table Learning with LLMs" paper.
# ArXiv: https://arxiv.org/abs/2407.20157

# Datasets  TACM12K
# Acc       0.324

import time
import argparse
import os.path as osp
import pandas as pd
import sys

sys.path.append("../")
sys.path.append("../../")

import torch
import torch.nn.functional as F

import rllm.transforms.graph_transforms as T
from rllm.datasets import TACM12KDataset
from rllm.nn.models import Bridge
from rllm.transforms.graph_transforms import build_homo_graph


parser = argparse.ArgumentParser()
parser.add_argument(
    "--tab_dim", type=int, default=256, help="Tab Transformer categorical embedding dim"
)
parser.add_argument("--gcn_dropout", type=float, default=0.5, help="Dropout for GCN")
parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
parser.add_argument("--lr", type=float, default=0.001, help="Learning rate")
parser.add_argument("--wd", type=float, default=5e-4, help="Weight decay")
args = parser.parse_args()

# Prepare datasets
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
path = osp.join(osp.dirname(osp.realpath(__file__)), "../..", "data")
dataset = TACM12KDataset(cached_dir=path, force_reload=True)
(
    paper_table,
    author_table,
    cite_table,
    writing_table,
    paper_embeddings,
    author_embeddings,
) = dataset.data_list

cite = cite_table.df.assign(Target=cite_table.df["paper_id_cited"])
author2id = {
    author_id: idx + paper_embeddings.size(0)
    for idx, author_id in enumerate(author_table.df.index.to_numpy())
}
writed = writing_table.df.assign(Target=writing_table.df["author_id"].map(author2id))

# Get relation with cite_table and writing_table
relation_df = pd.concat(
    [cite.iloc[:, [0, 2]], writed.iloc[:, [0, 2]]], axis=0, ignore_index=True
)
x = torch.cat([paper_embeddings, author_embeddings], dim=0)

# Making graph
emb_size = x.size(1)
graph = build_homo_graph(
    df=relation_df,
    n_src=len(paper_table),
    n_tgt=len(author_table),
    x=x,
    y=paper_table.y.long(),
    transform=T.GCNNorm(),
)
graph.paper_table = paper_table
graph.author_table = author_table
graph = graph.to(device)
train_mask, val_mask, test_mask = (
    graph.paper_table.train_mask,
    graph.paper_table.val_mask,
    graph.paper_table.test_mask,
)
output_dim = graph.paper_table.num_classes


def accuracy_score(preds, truth):
    return (preds == truth).sum(dim=0) / len(truth)


def train_epoch() -> float:
    model.train()
    optimizer.zero_grad()
    logits = model(
        graph.paper_table,
        graph.x,
        graph.adj,
        len(paper_table),
        len(paper_table) + len(author_table),
    )
    loss = F.cross_entropy(logits[train_mask].squeeze(), graph.y[train_mask])
    loss.backward()
    optimizer.step()
    return loss.item()


@torch.no_grad()
def test_epoch():
    model.eval()
    logits = model(
        graph.paper_table,
        graph.x,
        graph.adj,
        len(paper_table),
        len(paper_table) + len(author_table),
    )
    preds = logits.argmax(dim=1)
    y = graph.y
    train_acc = accuracy_score(preds[train_mask], y[train_mask])
    val_acc = accuracy_score(preds[val_mask], y[val_mask])
    test_acc = accuracy_score(preds[test_mask], y[test_mask])
    return train_acc.item(), val_acc.item(), test_acc.item()


model = Bridge(
    table_hidden_dim=emb_size,
    graph_output_dim=output_dim,
    stats_dict=graph.paper_table.stats_dict,
    graph_dropout=args.gcn_dropout,
    graph_layers=2,
    graph_hidden_dim=128,
).to(device)

start_time = time.time()
best_val_acc = best_test_acc = 0
optimizer = torch.optim.Adam(
    [
        dict(params=model.table_encoder.parameters(), lr=0.001),
        dict(params=model.graph_encoder.parameters(), lr=0.01, weight_decay=1e-4),
    ]
    # model.parameters(),
    # lr=args.lr,
    # weight_decay=args.wd
)

for epoch in range(1, args.epochs + 1):
    train_loss = train_epoch()
    train_acc, val_acc, test_acc = test_epoch()
    print(
        f"Epoch: [{epoch}/{args.epochs}]"
        f"Loss: {train_loss:.4f} train_acc: {train_acc:.4f} "
        f"val_acc: {val_acc:.4f} test_acc: {test_acc:.4f} "
    )
    if val_acc > best_val_acc:
        best_val_acc = val_acc
        best_test_acc = test_acc

print(f"Total Time: {time.time() - start_time:.4f}s")
print(
    "Bridge result: "
    f"Best Val acc: {best_val_acc:.4f}, "
    f"Best Test acc: {best_test_acc:.4f}"
)
