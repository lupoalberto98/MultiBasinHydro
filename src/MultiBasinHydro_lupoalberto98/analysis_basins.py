import numpy as np
import pandas as pd
import os
import argparse
from sklearn.manifold import TSNE
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnchoredText
import datetime
import seaborn as sns

# pytorch
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, random_split, ConcatDataset, Subset
import pytorch_lightning as pl
from pytorch_lightning import Trainer, seed_everything
from pytorch_lightning.callbacks import ModelCheckpoint

import torch.optim as optim
from pytorch_lightning.callbacks.early_stopping import EarlyStopping 
from torchvision import transforms, datasets
import multiprocessing


# user functions
from dataset import CamelDataset
from models import Hydro_LSTM_AE, Hydro_LSTM
from utils import Scale_Data, MetricsCallback, NSELoss, Globally_Scale_Data

def parse_args():
    parser=argparse.ArgumentParser(description="Take model id and best model epoch to analysis on test dataset")
    parser.add_argument('--model_ids', type=list, required=True, help="Identity of the model to analyize")
    parser.add_argument('--best_epochs', type=list, required=True, help="Epoch where best model (on validation dataset) is obtained")
    args=parser.parse_args()
    return args


if __name__ == '__main__':
    ##########################################################
    # set seed
    ##########################################################
    torch.manual_seed(42)
    np.random.seed(42)
    ##########################################################
    # dataset and dataloaders
    ##########################################################
    # Dataset
    #dates = ["1989/10/01", "2009/09/30"] 
    dates = ["1980/10/01", "2010/09/30"] # interval dates to pick
    force_attributes = ["prcp(mm/day)", "srad(W/m2)", "tmin(C)", "tmax(C)", "vp(Pa)"] # force attributes to use
    camel_dataset = CamelDataset(dates, force_attributes)
    #dataset.adjust_dates() # adjust dates if necessary
    camel_dataset.load_data() # load data
    num_basins = camel_dataset.__len__()
    seq_len = camel_dataset.seq_len
    print("Number of basins: %d" %num_basins)
    print("Sequence length: %d" %seq_len)

    ### Set proper device and train
    # check cpus and gpus available
    num_cpus = multiprocessing.cpu_count()
    print("Num of cpus: %d"%num_cpus)
    num_gpus = torch.cuda.device_count()
    print("Num of gpus: %d"%num_gpus)
    
    device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
    print(f"Training device: {device}")

    ### define reverse transformation
    transform_input = Globally_Scale_Data(camel_dataset.min_flow, camel_dataset.max_flow)
    transform_output = Globally_Scale_Data(camel_dataset.min_force, camel_dataset.max_force)

    ### Dataloader
    batch_size = 32
    # split 80/10/10
    num_workers = 0
    print("Number of workers: %d"%num_workers)

    num_train_data = int(num_basins * 0.7) 
    num_val_data = int(num_basins * 0.15) 
    num_test_data = num_basins - num_train_data - num_val_data
    print("Train basins: %d" %num_train_data)
    print("Validation basins: %d" %num_val_data)
    print("Test basins: %d" %num_test_data)
    train_dataset, val_dataset, test_dataset = random_split(camel_dataset, (num_train_data, num_val_data, num_test_data))
    #train_dataloader = DataLoader(train_dataset, batch_size=batch_size, num_workers=num_workers, shuffle=True,  drop_last=False)
    #val_dataloader = DataLoader(val_dataset, batch_size=batch_size, num_workers=num_workers, shuffle=False)
    # entire test dataset as one batch
    test_dataloader = DataLoader(test_dataset, batch_size=num_test_data, num_workers=num_workers, shuffle=False)
    split_indices = test_dataset.indices
    basin_names = [camel_dataset.trimmed_basin_names[idx] for idx in split_indices]
    print("Split indices for test dataset: ", split_indices)

    # load best model
    model_ids = ["lstm", "lstm-ae"]
    best_epochs = ["4739","4699"]
    model_dict = {
        "lstm-ae" : "LSTM-AE-27-Features",
        "lstm" : "LSTM",
    }
    start_date = datetime.datetime.strptime(dates[0], '%Y/%m/%d').date()
    # get data 
    x, y = next(iter(test_dataloader))
    x_unnorm = transform_input.reverse_transform(x.detach()).squeeze().numpy()
    # build figure
    length_to_plot = 730 # 2 years
    basins_n = 6
    fig, axs = plt.subplots(basins_n,basins_n, figsize=(30,30), sharey=True, sharex=True)
    fig1, axs1 = plt.subplots(basins_n,basins_n, figsize=(30,30), sharey=True, sharex=True)
    
    # basin idxs and start sequences
    start_sequences_list = np.random.randint(0, seq_len-length_to_plot, size=basins_n**2)

    # define loss function
    fig_nse, axs_nse = plt.subplots(1,2, figsize=(20,10))
    loss_fn = NSELoss(reduction=None)
    nse_df = pd.DataFrame()

    ###################################################################################
    # PLOT
    ###################################################################################
    # plot true one
    # plot some sequences
    for i in range(basins_n):
        for j in range(basins_n):
            ax = axs[i,j]
            ax1 = axs1[i,j]
            val = i*basins_n + j
            basin_name = basin_names[val]
            start_seq = start_sequences_list[val]
            date = start_date + datetime.timedelta(days=int(start_seq))
            time = date.strftime("%Y/%m/%d")
            ax.plot(x_unnorm[val, start_seq:start_seq+length_to_plot], label="Camel")
            ax.set_title("Start date: "+time, style='italic')
            at = AnchoredText(basin_name,loc='upper left', prop=dict(size=8), frameon=True)
            ax.add_artist(at)
            ax1.set_title("Start date: "+time, style='italic')
            at1 = AnchoredText(basin_name,loc='upper left', prop=dict(size=8), frameon=True)
            ax1.add_artist(at1)

    for count in range(len(model_ids)):
        model_id = model_ids[count]
        best_epoch = best_epochs[count].rjust(2,"0")
        path = os.path.join("checkpoints", model_id,"hydro-"+model_id+"-epoch="+best_epoch+".ckpt")
        if model_id =="lstm-ae" or model_id =="lstm-ae-nf5":
            model = Hydro_LSTM_AE.load_from_checkpoint(path)
            model.eval()
            # compute squeezed encoded representation and reconstruction
            enc, rec = model(x,y)

        else:
            model = Hydro_LSTM.load_from_checkpoint(path)
            rec = model(y)

        # compute NSE and save in dataframe
        nse_df[model_dict[model_id]] = - loss_fn(x.squeeze(), rec.squeeze()).detach().numpy() # array of size (num_test_data)
        
        # unnormalize input and output
        rec = transform_input.reverse_transform(rec.detach()).squeeze().numpy()
        # # perform tsne over encoded space
        # enc_embedded = TSNE(n_components=2, perplexity=1.0).fit_transform(enc)

        # fig1, ax1 = plt.subplots(1,1,figsize=(5,5))
        # ax1.scatter(enc_embedded[:,0], enc_embedded[:,1])
        # fig1.savefig("encoded_space-"+args.model_id+"-epoch="+str(args.best_epoch)+".png")

        # plot some sequences
        for i in range(basins_n):
            for j in range(basins_n):
                ax = axs[i,j]
                ax1 = axs1[i,j]
                val = i*basins_n + j
                start_seq = start_sequences_list[val]
                ax.plot(rec[val, start_seq:start_seq+length_to_plot], label=model_dict[model_id])
                ax1.semilogy(np.absolute(rec[val, start_seq:start_seq+length_to_plot]-x_unnorm[val, start_seq:start_seq+length_to_plot]), label=model_dict[model_id])

    # plot empirical kde nse distributions and comulatives
    stat = nse_df.describe()
    sns.kdeplot(nse_df, ax=axs_nse[0], legend=True, binrange=[0.0,1.0])
    sns.ecdfplot(nse_df, ax=axs_nse[1], legend=True, binrange=[0.0,1.0])
    axs_nse[0].set_ylabel("PDF")
    axs_nse[1].set_ylabel("CDF")
    handles, labels = axs_nse[0].get_legend_handles_labels()
    fig_nse.legend(handles, labels, loc='upper left', fontsize=50)
    fig_nse.savefig("nse_distribution.png")
    
    # return and save the figure of runoff
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper left', fontsize=50)
    fig.text(0.5, 0.04, 'Time (days)', ha='center', fontsize=50)
    fig.text(0.04, 0.5, 'Streamflow (mm/day)', va='center', rotation='vertical', fontsize=20)
    fig.tight_layout
    fig.savefig("reconstructed-best-epochs.png")

    # return and save the figure of runoff
    handles, labels = ax1.get_legend_handles_labels()
    fig1.legend(handles, labels, loc='upper left', fontsize=50)
    fig1.text(0.5, 0.04, 'Time (days)', ha='center', fontsize=50)
    fig1.text(0.04, 0.5, 'Delta Streamflow (mm/day)', va='center', rotation='vertical', fontsize=20)
    fig1.tight_layout
    fig1.savefig("abs-diff-best-epochs.png")

   
    

    

    
    
    