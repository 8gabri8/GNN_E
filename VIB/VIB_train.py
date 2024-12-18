#################
### Libraries
#################

import sys
import os

os.chdir("/home/dalai/GNN_E")
print(os.getcwd())

# Import our libraries
scripts_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../scripts'))
sys.path.append(scripts_path)
scripts_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../VIB'))
sys.path.append(scripts_path)
scripts_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(scripts_path)
from utils_models import *
from train_eval import *
import gsl
from torchsummary import summary


import pandas as pd
from math import ceil
import gc
from types import SimpleNamespace
from pathlib import Path
import random
import json
import argparse
import dask.dataframe as dd


# Torch Libraries
import torch
import torch.optim as optim
from torch.utils.checkpoint import checkpoint

#################
### Set Random Seeds
#################

np.random.seed(42)
random.seed(42)
torch.manual_seed(42)
torch.cuda.manual_seed(42)

#################
### Hyperparamters
#################

parser = argparse.ArgumentParser()
parser.add_argument("--params", type=str, help="Path to JSON file containing parameters.")
args_cli = parser.parse_args()

if args_cli.params != None: # In case passed from outer scirpt
    print(f"\nParamters from {args_cli.params}")
    # Load parameters from JSON file
    with open(args_cli.params, "r") as f:
        params = json.load(f)

    # Convert the dictionary to a SimpleNamespace
    args = SimpleNamespace(**params)
else:
    print(f"\nManually Entered parameters.")
    args = SimpleNamespace(

        type_prediction =  "all_emo", #all_emo, only ione emo e.e. "1"
        type_dataset =  "balanced", #balanced, unbalanced
        how_many_movies =  1, #how mna movies use to test the model, 1, 6, ...
        gpu_id = "0",

        ### DATASET PARAMETERS
        num_classes=13, #number of emotions to predict
        type_labels="single", #how the labels are encoded (single value, multi class one hot found with thr, ...)
        batch_size= 16,
        test_batch_size=16,
        percentage_train=0.8,
        percentage_val=0.0,
        test_train_splitting_mode="Vertical", #How to split between train and test
            # Vertical
            # Horizontal
        window_half_size=5, # Size of the window to use to create initial feautures
        node_feat="symmetricwindow", #how to create the node feautres
        initial_adj_method="clique", # hwo to initialize adge attr and edge connections
            # clique
            # FN_edgeAttr_FC_window
        FN="Limbic", #functional method in case the iniial grpah is a subset of nodes
        thr_FC = 0.7,
        
        ### VIB PARAMETERS
        dataset_name="EMOTION",
        backbone="GAT", # After graph leaning, VIB uses this backbone to precit the mu and std vectors
            # GAT, GIN, GCN
        hidden_dim=64, #hidden dim of the backbone
        num_layers=4, #number layer in case backbon eins GIN
        graph_type="prob", #how the new graph is learnt
            # epsiloNN --> Nodes are connected if their similarity (or attention score) is greater than epsilon.
            # prob -->  probabilistically, where the edges are determined by a Bernoulli distribution parameterized by the attention scores.
            # KNN --> each node to its k nearest neighbors based on the attention or similarity scores.
        top_k=10, # in case the graph is learnt with KNN
        epsilon=0.5, # in case the graph is elant using threhsolding
        graph_metric_type="mlp", #how to calculte similary between nodes in strucutre alening
            # attention
            # weighted_cosine --> cosine similarity but with learnable weights for each feature dimension
            # cosine --> raw cosine similarity
            # gat_attention --> graph attention mechanism inspired by Graph Attention Networks (GAT), using pairwise scores with a leaky ReLU activation
            # kernel --> Gaussian kernel with learnable precision to compute distances
            # transformer -->  Transformer-style attention, using query-key dot products scaled by feature dimensionality for similarity computation.
            # mlp --> multi-layer perceptron (MLP) to transform features and compute pairwise similarity.
            # multi-mlp
        num_per=13, # how many perpsctive use for graph_metric_type, ex if gat_attention (how mnay heads), if multi-mlp (how mnay indepdnet mlp)
        feature_denoise = False, 
            #enables or disables feature denoising during graph learning.
            # masl useless feaures
            # the mask is learnt
            # if true only a subsampel of feat are used for graph learning
        repar=True,
        beta=0.00001,# Weighting factor for the KL divergence in the VIB loss.
            #High beta: Enforces a more compact latent representation, which can lead to better generalization by removing noise but may hurt task accuracy if too much useful information is discarded.
            #Low beta: Retains more information in the latent representation, which may improve accuracy but risks overfitting or encoding noise.
        IB_size=32, # emb dimension (i.e lenght of mu and std) (nb the last layer of gnn is double this value)
        graph_skip_conn=0.0, # between [0-1], The new adjacency matrix is a linear combination of the initial adjacency matrix and the learned adjacency matrix
        graph_include_self=True, # add self loops in new adj if graph_skip_conn==0

        ### VIB TRAINING
        folds=10,
        epochs=10,
        lr=0.0001,
        lr_decay_factor=0.5,
        lr_decay_step_size=50,
        weight_decay=5e-5,
    )
print(args)

#################
###  Choose GPU to use
#################

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")
if device.type == "cuda":
    print(f"Device name: {torch.cuda.get_device_name(0)}")
    #If you set CUDA_VISIBLE_DEVICES=2, then GPU 2 (from the system’s perspective) becomes GPU 0 from the script's perspective.
print(torch.__version__)

#################
###  Load df all Movies
#################

if args.type_dataset == "balanced":
    #df_all_movies = pd.read_csv(f"data/processed/all_movies_labelled_13_single_balanced.csv")
    df_all_movies = dd.read_csv("data/processed/all_movies_labelled_13_single_balanced.csv")
    df_all_movies = df_all_movies.compute()
if args.type_dataset == "unbalanced":
    df_all_movies = pd.read_csv(f"data/processed/all_movies_labelled_{args.num_classes}_{args.type_labels}.csv")

# ATTENTION: only to test
#df_all_movies.loc[df_all_movies.label == 12, "label"] = -1

if args.type_prediction == "all_emo":
    pass
else: #single emo
    # all oher emo gain specific class "77"
    # problem now of unbalance
    df_all_movies.loc[~ df_all_movies.label.isin([int(x) for x in args.type_prediction]), "label"] = 77


if args.how_many_movies == 1:
    df_all_movies = df_all_movies[df_all_movies.movie.isin([9,13])]
if args.how_many_movies == 8:
    df_all_movies = df_all_movies[df_all_movies.movie.isin([4,9,    1,2,3,5,6])]
else: #use all
    pass

#################
### Split in Train, Validation, Test
#################

print(f"Splitting {args.test_train_splitting_mode}...")


if args.test_train_splitting_mode == "Vertical":
    if args.how_many_movies == 1:
        dict_test_movies = {"FirstBite": 4, "YouAgain": 13}
    else:
        dict_test_movies = {"FirstBite": 4, "Superhero": 9, "YouAgain": 13}

    df_train, df_test = split_train_test_vertically(
        df_all_movies, 
        test_movies_dict = dict_test_movies)
    df_val = df_train[df_train.id == 99] #make sure to be empty

elif args.test_train_splitting_mode == "Horizontal":
    df_train, df_val, df_test = split_train_val_test_horizontally(
        df_all_movies, 
        percentage_train=args.percentage_train, 
        percentage_val=args.percentage_val, #0 to not have nay val set
        path_pickle_delay="data/raw/labels/run_onsets.pkl",
        path_movie_title_mapping="data/raw/labels/category_mapping_movies.csv", 
        tr_len=1.3
    )
elif args.test_train_splitting_mode == "MovieRest":
    df_rest = pd.read_csv("data/raw/rest/Rest_compiled414_processed.csv")
    df_train, df_test = split_train_test_rest_classification(df_all_movies, df_rest)
    df_val = df_train[df_train.id == 99] #make sure to be empty

# if we want only one sub
if args.use_one_sub:
    print("Subsampling to use only one sub.")
    df_train.loc[~((df_train.id.isin(np.arange(2,3)))), "label"] = -1
    df_test.loc[~((df_test.id.isin(np.arange(2,3)))), "label"] = -1
    print(df_train[df_train.label != -1].shape[0] / 414)
    print(df_test[df_test.label != -1].shape[0] / 414)

#################
### Create dataset (i.e. graph list)
#################

dataset_train = DatasetEmo_fast(
    df = df_train, #df with mvoies to use
    node_feat = args.node_feat, #"singlefmri", "symmetricwindow", "pastwindow"
    initial_adj_method = args.initial_adj_method, #"clique_edgeAttr_FC_window", #"FN_edgeAttr_FC_window",
        # "clique"
        #FC dynamic:  "fcmovie", "fcwindow"
        #FN (subcorticla with clique): "FN_const" "FN_edgeAttr_FC_window" "FN_edgeAttr_FC_movie"
    FN = args.FN,  #['Vis' 'SomMot' 'DorsAttn' 'SalVentAttn' 'Limbic' 'Cont' 'Default' 'Sub']
    FN_paths = "data/raw/FN_raw",
    sizewind = args.window_half_size,
    verbose = False,
    thr_FC = args.thr_FC, #big windows requires smoaller thr
    kernelize_feat = args.kernelize_feat
)

dataset_val = DatasetEmo_fast(
    df = df_val,
    node_feat = args.node_feat, 
    initial_adj_method = args.initial_adj_method,
    FN_paths = "data/raw/FN_raw",
    sizewind = args.window_half_size,
    verbose = False,
    thr_FC = args.thr_FC,
    kernelize_feat = args.kernelize_feat
)

dataset_test = DatasetEmo_fast(
    df = df_test,
    node_feat = args.node_feat, 
    initial_adj_method = args.initial_adj_method,
    FN = args.FN, 
    FN_paths = "data/raw/FN_raw",
    sizewind = args.window_half_size,
    verbose = False,
    thr_FC = args.thr_FC,
    kernelize_feat = args.kernelize_feat
)

# Extarct the list of graphs of each dataset
graphs_list_train = dataset_train.get_graphs_list()
graphs_list_val = dataset_val.get_graphs_list()
graphs_list_test = dataset_test.get_graphs_list()

print()
print(f"Number Batces Train {len(graphs_list_train)/args.batch_size}")
print(f"Number Batces Val {len(graphs_list_val)/args.batch_size}")
print(f"Number Batces Test {len(graphs_list_test)/args.batch_size}")


#################
### Istantiate the Model
#################

# Number fo features for each node
num_node_features = graphs_list_train[0].x.shape[1]
print("\nnum_node_features : %d, num_classes : %d"%(num_node_features, args.num_classes))

model = gsl.VIBGSL(
            args, 
            num_node_features, 
            args.num_classes)
print(model.__repr__())

# Calculate the total number of trainable parameters
total_trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
print(f"\nTotal number of trainable parameters: {total_trainable_params}\n")

#################
### Train
#################

# Useful if the code get some strange anomaly
torch.autograd.set_detect_anomaly(True)

# the model is chnaged in place
train_losses, train_accs, test_losses, test_accs = my_train_and_evaluate(
    train_graphs_list = graphs_list_train,
    test_graphs_list = graphs_list_test,
    model = model,
    epochs = args.epochs, 
    batch_size = args.batch_size, 
    test_batch_size = args.test_batch_size,
    lr = args.lr, 
    lr_decay_factor = args.lr_decay_factor, 
    lr_decay_step_size = args.lr_decay_step_size,
    weight_decay = args.weight_decay, 
)

#################
### Evaluate on test
#################

# Extarct accuracy, learnt grpahs and predicted lablled in train set
acc_train, new_graphs_list_train, pred_y_train = my_interpretation(
        graphs_list = graphs_list_train,
        model_trained = model,
        batch_size = args.batch_size,
)

# Extarct accuracy, learnt grpahs and predicted lablled in test set
acc_test, new_graphs_list_test, pred_y_test = my_interpretation(
        graphs_list = graphs_list_test,
        model_trained = model,
        batch_size = args.batch_size,
)

# Extarct ground.truth labels
pred_y_train = [y.item() for y in pred_y_train]
y_train = [g.y.item() for g in graphs_list_train]
pred_y_test = [y.item() for y in pred_y_test]
y_test = [g.y.item() for g in graphs_list_test]

# Create a dictionary where the keys are labels and the values are lists of adjacency matrices
dict_new_graphs_list_test_adj = {}
for g, label in zip(new_graphs_list_test, y_test):
    label = str(label)  # Convert the label to string
    adj = to_dense_adj(edge_index=g.edge_index, edge_attr=g.edge_attr)
    adj = adj.cpu().squeeze().numpy()  # Convert to numpy array
    if label not in dict_new_graphs_list_test_adj:
        dict_new_graphs_list_test_adj[label] = []  # Create a list for this label if it doesn't exist
    dict_new_graphs_list_test_adj[label].append(adj)

#################
### Save Results
#################

# best_acc_train = max(train_accs)
# best_acc_test = max(test_accs)
print(f"Best Acc Train: {acc_train}\nBest Acc Tets: {acc_test}")

# Create Dir results
RESULT_DIR = Path(f"data/results/VIB/{args.type_dataset}/{args.how_many_movies}/{args.type_prediction}/{int(acc_train*10000)}-{int(acc_test*10000)}")
print(RESULT_DIR)
os.makedirs(RESULT_DIR, exist_ok=True)

# Convert SimpleNamespace to dictionary
results_dict = vars(args)
# Create dict with all results
results_dict.update(
    {
        "train_losses": train_losses,
        "test_losses": test_losses,

        "train_accs": train_accs,
        "test_accs": test_accs, 

        "final_acc_train": acc_train,
        "final_acc_test": acc_test,

        "pred_y_train": pred_y_train, 
        "pred_y_test": pred_y_test, 

        "y_train": y_train,
        "y_test": y_test,
    }
)

# Save dict with hyperpetes and results
with open(os.path.join(RESULT_DIR, 'results.json'), 'w') as f:
    json.dump(results_dict, f, indent=4)

# Save the entire model (architecture + weights)
torch.save(model, os.path.join(RESULT_DIR, 'full_model.pth'))

# Save test adk mayrices
#np.savez_compressed(os.path.join(RESULT_DIR, 'adj_test.npz'), **dict_new_graphs_list_test_adj, labels=y_test)






# For future Loading of the model
#model = torch.load('full_model.pth')
#model = model.to(torch.device('cuda' if torch.cuda.is_available() else 'cpu'))



