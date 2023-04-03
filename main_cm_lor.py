import torch
from datetime import datetime

from filters.EKF_test import EKFTest

from simulations.Extended_sysmdl import SystemModel
from simulations.utils import DataGen
import simulations.config as config
from simulations.lorenz_attractor.parameters import m1x_0, m2x_0, m, n,\
f, h, h_nonlinear, Q_structure, R_structure

from hnets.hnet import HyperNetwork
from mnets.KNet_mnet import KalmanNetNN as KNet_mnet

from pipelines.Pipeline_cm import Pipeline_cm
from pipelines.Pipeline_EKF import Pipeline_EKF

print("Pipeline Start")
################
### Get Time ###
################
today = datetime.today()
now = datetime.now()
strToday = today.strftime("%m.%d.%y")
strNow = now.strftime("%H:%M:%S")
strTime = strToday + "_" + strNow
print("Current Time =", strTime)

###################
###  Settings   ###
###################
args = config.general_settings()
args.use_cuda = True # use GPU or not
if args.use_cuda:
   if torch.cuda.is_available():
      device = torch.device('cuda')
      print("Using GPU")
      torch.set_default_tensor_type(torch.cuda.FloatTensor)
   else:
      raise Exception("No GPU found, please set args.use_cuda = False")
else:
    device = torch.device('cpu')
    print("Using CPU")
### dataset parameters
Q_structure = Q_structure.to(device)
R_structure = R_structure.to(device)
m1x_0 = m1x_0.to(device)
args.N_E = 1000
args.N_CV = 100
args.N_T = 200
args.T = 20
args.T_test = 20
### settings for KalmanNet
args.in_mult_KNet = 40
args.out_mult_KNet = 5

### training parameters
args.wandb_switch = True
if args.wandb_switch:
   import wandb
   wandb.init(project="HKNet_Lor")
# training parameters for KalmanNet
args.knet_trainable = True
args.n_steps = 10000
args.n_batch = 100
args.lr = 1e-3
args.wd = 1e-3
args.CompositionLoss = True
args.alpha = 0.5
# training parameters for Hypernet
n_steps = 1000
n_batch = 100 # will be multiplied by num of datasets
lr = 1e-3
wd = 1e-4

### True model
# SoW
SoW = torch.tensor([[0,0,1,0.1], [0,0,1,0.4], [0,0,1,0.7], [0,0,1,1], [0,0,1,0.15], [0,0,1,0.55], [0,0,1,0.9]])
SoW_train_range = [0,1,2,3] # first *** number of datasets are used for training
SoW_test_range = [0,1,2,3,4,5,6] # last *** number of datasets are used for testing
# noise
r2 = SoW[:, 2]
q2 = SoW[:, 3]
for i in range(len(SoW)):
   print(f"SoW of dataset {i}: ", SoW[i])
   print(f"r2 [linear] and q2 [linear] of dataset  {i}: ", r2[i], q2[i])
# model
sys_model = []
for i in range(len(SoW)):
   sys_model_i = SystemModel(f, q2[i]*Q_structure, h_nonlinear, r2[i]*R_structure, args.T, args.T_test, m, n)# parameters for GT
   sys_model_i.InitSequence(m1x_0, m2x_0)# x0 and P0
   sys_model.append(sys_model_i)

### paths 
path_results = 'simulations/lorenz_attractor/results/'
DatafolderName = 'data/lorenz_attractor/'
# traj_resultName = ['traj_lorDT_NLobs_rq3030_T20.pt']
dataFileName = []
for i in range(len(SoW)):
   dataFileName.append('r2=' + str(r2[i].item())+"_" +"q2="+ str(q2[i].item())+ '.pt')

#########################################
###  Generate and load data DT case   ###
#########################################
# print("Start Data Gen")
# for i in range(len(SoW)):
#    DataGen(args, sys_model[i], DatafolderName + dataFileName[i])
print("Data Load")
train_input_list = []
train_target_list = []
cv_input_list = []
cv_target_list = []
test_input_list = []
test_target_list = []
train_init_list = []
cv_init_list = []
test_init_list = []
for i in range(len(SoW)):
   [train_input,train_target, cv_input, cv_target, test_input, test_target,train_init, cv_init, test_init] =  torch.load(DatafolderName + dataFileName[i], map_location=device)   
   
   train_input_list.append((train_input, SoW[i]))
   train_target_list.append((train_target, SoW[i]))
   cv_input_list.append((cv_input, SoW[i]))
   cv_target_list.append((cv_target, SoW[i]))
   test_input_list.append((test_input, SoW[i]))
   test_target_list.append((test_target, SoW[i]))
   train_init_list.append(train_init)
   cv_init_list.append(cv_init)
   test_init_list.append(test_init)

########################
### Evaluate Filters ###
########################
# ### Evaluate EKF full
print("Evaluate EKF full")
for i in range(len(SoW)):
   test_input = test_input_list[i][0]
   test_target = test_target_list[i][0]
   test_init = test_init_list[i][0]
   print(f"Dataset {i}")
   [MSE_EKF_linear_arr, MSE_EKF_linear_avg, MSE_EKF_dB_avg, EKF_KG_array, EKF_out] = EKFTest(args, sys_model[i], test_input, test_target)

# ### Save trajectories
# trajfolderName = 'Filters' + '/'
# DataResultName = traj_resultName[0]
# EKF_sample = torch.reshape(EKF_out[0],[1,m,args.T_test])
# target_sample = torch.reshape(test_target[0,:,:],[1,m,args.T_test])
# input_sample = torch.reshape(test_input[0,:,:],[1,n,args.T_test])
# torch.save({
#             'EKF': EKF_sample,
#             'ground_truth': target_sample,
#             'observation': input_sample,
#             }, trajfolderName+DataResultName)


##################################
### Hyper - KalmanNet Pipeline ###
##################################
### train and test KalmanNet on dataset 0
print("KalmanNet pipeline start, train on dataset 0")
KalmanNet_model = KNet_mnet()
KalmanNet_model.NNBuild(sys_model[0], args)
print("Number of trainable parameters for KalmanNet:",sum(p.numel() for p in KalmanNet_model.parameters() if p.requires_grad))
## Train Neural Network
KalmanNet_Pipeline = Pipeline_EKF(strTime, "KNet", "KalmanNet")
KalmanNet_Pipeline.setssModel(sys_model[0])
KalmanNet_Pipeline.setModel(KalmanNet_model)
KalmanNet_Pipeline.setTrainingParams(args)
KalmanNet_Pipeline.NNTrain(sys_model[0], cv_input_list[0][0], cv_target_list[0][0], train_input_list[0][0], train_target_list[0][0], path_results)
## save model weights
torch.save(KalmanNet_Pipeline.model.state_dict(), path_results + 'knet_frozen_weights.pt')
frozen_weights = torch.load(path_results + 'knet_frozen_weights.pt', map_location=device) 

### frozen KNet weights, train hypernet to generate CM weights on multiple datasets
args.knet_trainable = False # frozen KNet weights
args.use_context_mod = True # use CM
## training parameters for Hypernet
args.n_steps = n_steps
args.n_batch = n_batch # will be multiplied by num of datasets
args.lr = lr
args.wd = wd
## Build Neural Networks
print("Build HNet and KNet")
KalmanNet_model = KNet_mnet()
cm_weight_size = KalmanNet_model.NNBuild(sys_model[0], args, frozen_weights=frozen_weights)
print("Number of CM parameters:", cm_weight_size)
HyperNet_model = HyperNetwork(args, cm_weight_size)
weight_size_hnet = sum(p.numel() for p in HyperNet_model.parameters() if p.requires_grad)
print("Number of parameters for HyperNet:", weight_size_hnet)
print("Total number of parameters:", cm_weight_size + weight_size_hnet)
## Set up pipeline
hknet_pipeline = Pipeline_cm(strTime, "pipelines", "hknet")
hknet_pipeline.setModel(HyperNet_model, KalmanNet_model)
hknet_pipeline.setTrainingParams(args)
## Optinal: record parameters to wandb
if args.wandb_switch:
   wandb.log({
   "total_params": cm_weight_size + weight_size_hnet,
   "batch_size": args.n_batch,
   "learning_rate": args.lr,  
   "weight_decay": args.wd})
## Train Neural Networks
hknet_pipeline.NNTrain_mixdatasets(SoW_train_range, sys_model, cv_input_list, cv_target_list, train_input_list, train_target_list, path_results,cv_init_list,train_init_list)

## Test Neural Networks for each dataset  
hknet_pipeline.NNTest_alldatasets(SoW_test_range, sys_model, test_input_list, test_target_list, path_results,test_init_list)



## Close wandb run
if args.wandb_switch: 
   wandb.finish() 