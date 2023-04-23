"""Class: KalmanNet as main network

The hypernetwork is structured MLP.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init

class KalmanNetNN(torch.nn.Module):

    ###################
    ### Constructor ###
    ###################
    def __init__(self):
        super().__init__()
    
    def NNBuild(self, SysModel, args, frozen_weights=None):

        # Device
        if args.use_cuda:
            self.device = torch.device('cuda')
        else:
            self.device = torch.device('cpu')

        self.activation_func = nn.ReLU()
        
        assert(args.use_context_mod == True) # structured MLP is used for CM
        self.use_context_mod = args.use_context_mod # Use context modulation or not

        self.knet_trainable = args.knet_trainable # Train KNet or not

        self.InitSystemDynamics(SysModel.f, SysModel.h, SysModel.m, SysModel.n)

        self.InitKGainNet(SysModel.prior_Q, SysModel.prior_Sigma, SysModel.prior_S, args, frozen_weights=frozen_weights)
       
        return self.n_params_KNet

    ######################################
    ### Initialize Kalman Gain Network ###
    ######################################
    def InitKGainNet(self, prior_Q, prior_Sigma, prior_S, args, frozen_weights=None):

        self.seq_len_input = 1 # KNet calculates time-step by time-step
        self.batch_size = args.n_batch # Batch size

        self.prior_Q = prior_Q.to(self.device)
        self.prior_Sigma = prior_Sigma.to(self.device)
        self.prior_S = prior_S.to(self.device)

        self.out_Q = self.prior_Q.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)
        self.out_Sigma = self.prior_Sigma.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)
        self.out_S = self.prior_S.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)

        ### Define network dimensions ###
        # lstm to track Q
        d_input_Q = self.m * args.in_mult_KNet
        d_hidden_Q = self.m ** 2
        # lstm to track Sigma
        d_input_Sigma = d_hidden_Q + self.m * args.in_mult_KNet
        d_hidden_Sigma = self.m ** 2  
        # lstm to track S
        d_input_S = self.n ** 2 + 2 * self.n * args.in_mult_KNet
        d_hidden_S = self.n ** 2       
        # Fully connected 1
        d_input_FC1 = d_hidden_Sigma
        d_output_FC1 = self.n ** 2
        # Fully connected 2
        d_input_FC2 = d_hidden_S + d_hidden_Sigma
        d_output_FC2 = self.n * self.m
        d_hidden_FC2 = d_input_FC2 * args.out_mult_KNet
        # Fully connected 3
        d_input_FC3 = d_hidden_S + d_output_FC2
        d_output_FC3 = self.m ** 2
        # Fully connected 4
        d_input_FC4 = d_hidden_Sigma + d_output_FC3
        d_output_FC4 = d_hidden_Sigma       
        # Fully connected 5
        d_input_FC5 = self.m
        d_output_FC5 = self.m * args.in_mult_KNet
        # Fully connected 6
        d_input_FC6 = self.m
        d_output_FC6 = self.m * args.in_mult_KNet       
        # Fully connected 7
        d_input_FC7 = 2 * self.n
        d_output_FC7 = 2 * self.n * args.in_mult_KNet

        # Define original KNet fc and lstm layer shapes for later internal layer construction
        self.fc_shape = {
            'fc1_w': [d_output_FC1, d_input_FC1],
            'fc1_b': [d_output_FC1],
            'fc2_w1': [d_hidden_FC2, d_input_FC2],
            'fc2_b1': [d_hidden_FC2],
            'fc2_w2': [d_output_FC2, d_hidden_FC2],
            'fc2_b2': [d_output_FC2],
            'fc3_w': [d_output_FC3, d_input_FC3],
            'fc3_b': [d_output_FC3],
            'fc4_w': [d_output_FC4, d_input_FC4],
            'fc4_b': [d_output_FC4],
            'fc5_w': [d_output_FC5, d_input_FC5],
            'fc5_b': [d_output_FC5],
            'fc6_w': [d_output_FC6, d_input_FC6],
            'fc6_b': [d_output_FC6],
            'fc7_w': [d_output_FC7, d_input_FC7],
            'fc7_b': [d_output_FC7]}
        
        self.lstm_shape = {
            'lstm_q_w_ih': [d_hidden_Q * 4, d_input_Q],
            'lstm_q_b_ih': [d_hidden_Q * 4],
            'lstm_q_w_hh': [d_hidden_Q * 4, d_hidden_Q],
            'lstm_q_b_hh': [d_hidden_Q * 4],
            'lstm_sigma_w_ih': [d_hidden_Sigma * 4, d_input_Sigma],
            'lstm_sigma_b_ih': [d_hidden_Sigma * 4],
            'lstm_sigma_w_hh': [d_hidden_Sigma * 4, d_hidden_Sigma],
            'lstm_sigma_b_hh': [d_hidden_Sigma * 4],
            'lstm_s_w_ih': [d_hidden_S * 4, d_input_S],
            'lstm_s_b_ih': [d_hidden_S * 4],
            'lstm_s_w_hh': [d_hidden_S * 4, d_hidden_S],
            'lstm_s_b_hh': [d_hidden_S * 4]}
        
        if self.use_context_mod == True:
            self.cm_shape = {
                'lstm_q_ih_gain': d_hidden_Q * 4,
                'lstm_q_ih_shift': d_hidden_Q * 4,
                
                'lstm_sigma_ih_gain': d_hidden_Sigma * 4,
                'lstm_sigma_ih_shift': d_hidden_Sigma * 4,
                
                'lstm_s_ih_gain': d_hidden_S * 4,
                'lstm_s_ih_shift': d_hidden_S * 4}
            
            n_params_cm = d_hidden_Q * 4 * 2 + d_hidden_S * 4 * 2 + d_hidden_Sigma * 4 * 2
        
        ### Calculate number of parameters in KNet ###
        n_params_fc = d_output_FC1*(d_input_FC1 +1)+d_hidden_FC2*(d_input_FC2 +1)+d_output_FC2*(d_hidden_FC2 +1)+d_output_FC3*(d_input_FC3 +1)+d_output_FC4*(d_input_FC4 +1)+d_output_FC5*(d_input_FC5 +1)+d_output_FC6*(d_input_FC6 +1)+d_output_FC7*(d_input_FC7 +1)
        n_params_lstm = d_hidden_Q*(d_input_Q +1)*4+d_hidden_Sigma*(d_input_Sigma +1)*4+d_hidden_S*(d_input_S +1)*4 +\
                        d_hidden_Q * 4 * (d_hidden_Q +1) + d_hidden_Sigma * 4 * (d_hidden_Sigma +1) + d_hidden_S * 4 * (d_hidden_S +1)

        if self.use_context_mod == True:
            self.n_params_KNet = n_params_cm # hypernet only generate context mod weights
        else:
            self.n_params_KNet = n_params_fc + n_params_lstm
        
        ### Define KNet layers ###
        self._weights = nn.ParameterList()
        # Fully connected 1-7
        self.register_parameter('fc1_w', nn.Parameter(torch.Tensor(d_output_FC1, d_input_FC1)))
        self._weights.append(self.fc1_w)
        self.register_parameter('fc1_b', nn.Parameter(torch.Tensor(d_output_FC1)))
        self._weights.append(self.fc1_b)
        self.register_parameter('fc2_w1', nn.Parameter(torch.Tensor(d_hidden_FC2, d_input_FC2)))
        self._weights.append(self.fc2_w1)
        self.register_parameter('fc2_b1', nn.Parameter(torch.Tensor(d_hidden_FC2)))
        self._weights.append(self.fc2_b1)
        self.register_parameter('fc2_w2', nn.Parameter(torch.Tensor(d_output_FC2, d_hidden_FC2)))
        self._weights.append(self.fc2_w2)
        self.register_parameter('fc2_b2', nn.Parameter(torch.Tensor(d_output_FC2)))
        self._weights.append(self.fc2_b2)
        self.register_parameter('fc3_w', nn.Parameter(torch.Tensor(d_output_FC3, d_input_FC3)))
        self._weights.append(self.fc3_w)
        self.register_parameter('fc3_b', nn.Parameter(torch.Tensor(d_output_FC3)))
        self._weights.append(self.fc3_b)
        self.register_parameter('fc4_w', nn.Parameter(torch.Tensor(d_output_FC4, d_input_FC4)))
        self._weights.append(self.fc4_w)
        self.register_parameter('fc4_b', nn.Parameter(torch.Tensor(d_output_FC4)))
        self._weights.append(self.fc4_b)
        self.register_parameter('fc5_w', nn.Parameter(torch.Tensor(d_output_FC5, d_input_FC5)))
        self._weights.append(self.fc5_w)
        self.register_parameter('fc5_b', nn.Parameter(torch.Tensor(d_output_FC5)))
        self._weights.append(self.fc5_b)
        self.register_parameter('fc6_w', nn.Parameter(torch.Tensor(d_output_FC6, d_input_FC6)))
        self._weights.append(self.fc6_w)
        self.register_parameter('fc6_b', nn.Parameter(torch.Tensor(d_output_FC6)))
        self._weights.append(self.fc6_b)
        self.register_parameter('fc7_w', nn.Parameter(torch.Tensor(d_output_FC7, d_input_FC7)))
        self._weights.append(self.fc7_w)
        self.register_parameter('fc7_b', nn.Parameter(torch.Tensor(d_output_FC7)))
        self._weights.append(self.fc7_b)
        # LSTM Q, Sigma, S
        self.register_parameter('lstm_q_w_ih', nn.Parameter(torch.Tensor(d_hidden_Q * 4, d_input_Q)))
        self._weights.append(self.lstm_q_w_ih)
        self.register_parameter('lstm_q_b_ih', nn.Parameter(torch.Tensor(d_hidden_Q * 4)))
        self._weights.append(self.lstm_q_b_ih)
        self.register_parameter('lstm_q_w_hh', nn.Parameter(torch.Tensor(d_hidden_Q * 4, d_hidden_Q)))
        self._weights.append(self.lstm_q_w_hh)
        self.register_parameter('lstm_q_b_hh', nn.Parameter(torch.Tensor(d_hidden_Q * 4)))
        self._weights.append(self.lstm_q_b_hh)
        self.register_parameter('lstm_sigma_w_ih', nn.Parameter(torch.Tensor(d_hidden_Sigma * 4, d_input_Sigma)))
        self._weights.append(self.lstm_sigma_w_ih)
        self.register_parameter('lstm_sigma_b_ih', nn.Parameter(torch.Tensor(d_hidden_Sigma * 4)))
        self._weights.append(self.lstm_sigma_b_ih)
        self.register_parameter('lstm_sigma_w_hh', nn.Parameter(torch.Tensor(d_hidden_Sigma * 4, d_hidden_Sigma)))
        self._weights.append(self.lstm_sigma_w_hh)
        self.register_parameter('lstm_sigma_b_hh', nn.Parameter(torch.Tensor(d_hidden_Sigma * 4)))
        self._weights.append(self.lstm_sigma_b_hh)
        self.register_parameter('lstm_s_w_ih', nn.Parameter(torch.Tensor(d_hidden_S * 4, d_input_S)))
        self._weights.append(self.lstm_s_w_ih)
        self.register_parameter('lstm_s_b_ih', nn.Parameter(torch.Tensor(d_hidden_S * 4)))
        self._weights.append(self.lstm_s_b_ih)
        self.register_parameter('lstm_s_w_hh', nn.Parameter(torch.Tensor(d_hidden_S * 4, d_hidden_S)))
        self._weights.append(self.lstm_s_w_hh)
        self.register_parameter('lstm_s_b_hh', nn.Parameter(torch.Tensor(d_hidden_S * 4)))
        self._weights.append(self.lstm_s_b_hh)
        
        ### Initialize weights (load frozen weights/hypernet generates weights if not trainable, else apply Xavier and He initialization) ###       
        if self.knet_trainable == False:
            # load frozen weights if provided (else hypernet generates weights)
            if frozen_weights is not None:  
                model_state_dict = self.state_dict() # get the current state dict of the model
                model_state_dict.update(frozen_weights) # update the current state dict with the frozen KNet weights
                self.load_state_dict(model_state_dict) # load the updated state dict to the model
            # Block gradient flow if not trainable 
            for param in self.parameters():
                param.requires_grad = False
        
        else:
            # Apply Xavier initialization to LSTM layers              
            init.xavier_uniform_(self.lstm_q_w_ih.data)
            init.xavier_uniform_(self.lstm_q_w_hh.data)
            init.xavier_uniform_(self.lstm_sigma_w_ih.data)
            init.xavier_uniform_(self.lstm_sigma_w_hh.data)
            init.xavier_uniform_(self.lstm_s_w_ih.data)
            init.xavier_uniform_(self.lstm_s_w_hh.data)
                 
            self.lstm_q_b_ih.data.fill_(0)
            self.lstm_q_b_hh.data.fill_(0)
            self.lstm_sigma_b_ih.data.fill_(0)
            self.lstm_sigma_b_hh.data.fill_(0)
            self.lstm_s_b_ih.data.fill_(0)
            self.lstm_s_b_hh.data.fill_(0)


            # Apply He initialization to FC layers
            init.kaiming_uniform_(self.fc1_w.data, nonlinearity='relu')
            init.kaiming_uniform_(self.fc2_w1.data, nonlinearity='relu')
            init.kaiming_uniform_(self.fc2_w2.data, nonlinearity='relu')
            init.kaiming_uniform_(self.fc3_w.data, nonlinearity='relu')
            init.kaiming_uniform_(self.fc4_w.data, nonlinearity='relu')
            init.kaiming_uniform_(self.fc5_w.data, nonlinearity='relu')
            init.kaiming_uniform_(self.fc6_w.data, nonlinearity='relu')
            init.kaiming_uniform_(self.fc7_w.data, nonlinearity='relu')
                    
            self.fc1_b.data.fill_(0)
            self.fc2_b1.data.fill_(0)
            self.fc2_b2.data.fill_(0)
            self.fc3_b.data.fill_(0)
            self.fc4_b.data.fill_(0)
            self.fc5_b.data.fill_(0)
            self.fc6_b.data.fill_(0)
            self.fc7_b.data.fill_(0)
        
    @property
    def weights(self):
        """A list of all internal weights of this layer.

        If all weights are assumed to be generated externally, then this
        attribute will be ``None``.

        :type: torch.nn.ParameterList or None
        """
        return self._weights
    
    #######################
    ### System Dynamics ###
    #######################
    def InitSystemDynamics(self, f, h, m, n):
        
        # Set State Evolution Function
        self.f = f
        self.m = m

        # Set Observation Function
        self.h = h
        self.n = n

    def UpdateSystemDynamics(self, SysModel):
        
        # Set State Evolution Function
        self.f = SysModel.f
        self.m = SysModel.m

        # Set Observation Function
        self.h = SysModel.h
        self.n = SysModel.n

    ###########################
    ### Initialize Sequence ###
    ###########################
    def InitSequence(self, M1_0, T):
        """
        input M1_0 (torch.tensor): 1st moment of x at time 0 [batch_size, m, 1]
        """
        self.T = T

        self.m1x_posterior = M1_0.to(self.device)
        self.m1x_posterior_previous = self.m1x_posterior
        self.m1x_prior_previous = self.m1x_posterior
        self.y_previous = self.h(self.m1x_posterior)

    ######################
    ### Compute Priors ###
    ######################
    def step_prior(self):
        # Predict the 1-st moment of x
        self.m1x_prior = self.f(self.m1x_posterior)

        # Predict the 1-st moment of y
        self.m1y = self.h(self.m1x_prior)

    ##############################
    ### Kalman Gain Estimation ###
    ##############################
    def step_KGain_est(self, y):
        # both in size [batch_size, n]
        obs_diff = torch.squeeze(y,2) - torch.squeeze(self.y_previous,2) 
        obs_innov_diff = torch.squeeze(y,2) - torch.squeeze(self.m1y,2)
        # both in size [batch_size, m]
        fw_evol_diff = torch.squeeze(self.m1x_posterior,2) - torch.squeeze(self.m1x_posterior_previous,2)
        fw_update_diff = torch.squeeze(self.m1x_posterior,2) - torch.squeeze(self.m1x_prior_previous,2)

        obs_diff = F.normalize(obs_diff, p=2, dim=1, eps=1e-12, out=None)
        obs_innov_diff = F.normalize(obs_innov_diff, p=2, dim=1, eps=1e-12, out=None)
        fw_evol_diff = F.normalize(fw_evol_diff, p=2, dim=1, eps=1e-12, out=None)
        fw_update_diff = F.normalize(fw_update_diff, p=2, dim=1, eps=1e-12, out=None)

        # Kalman Gain Network Step
        KG = self.KGain_step(obs_diff, obs_innov_diff, fw_evol_diff, fw_update_diff)

        # Reshape Kalman Gain to a Matrix
        self.KGain = torch.reshape(KG, (self.batch_size, self.m, self.n))

    #######################
    ### Kalman Net Step ###
    #######################
    def KNet_step(self, y):

        # Compute Priors
        self.step_prior()

        # Compute Kalman Gain
        self.step_KGain_est(y)

        # Innovation
        dy = y - self.m1y # [batch_size, n, 1]

        # Compute the 1-st posterior moment
        INOV = torch.bmm(self.KGain, dy)
        self.m1x_posterior_previous = self.m1x_posterior
        self.m1x_posterior = self.m1x_prior + INOV

        #self.state_process_posterior_0 = self.state_process_prior_0
        self.m1x_prior_previous = self.m1x_prior

        # update y_prev
        self.y_previous = y

        # return
        return self.m1x_posterior

    ########################
    ### Kalman Gain Step ###
    ########################
    def KGain_step(self, obs_diff, obs_innov_diff, fw_evol_diff, fw_update_diff):

        def expand_dim(x):
            expanded = torch.empty(self.seq_len_input, self.batch_size, x.shape[-1]).to(self.device)
            expanded[0, :, :] = x
            return expanded

        obs_diff = expand_dim(obs_diff)
        obs_innov_diff = expand_dim(obs_innov_diff)
        fw_evol_diff = expand_dim(fw_evol_diff)
        fw_update_diff = expand_dim(fw_update_diff)
        
        ####################
        ### Forward Flow ###
        ####################     
        # FC 5
        in_FC5 = fw_evol_diff
        out_FC5 = self.activation_func(F.linear(in_FC5, self.fc5_w, bias=self.fc5_b))

        # Q-lstm
        in_Q = out_FC5     
        self.out_Q, self.h_Q = self.lstm_rnn_step(in_Q, (self.out_Q, self.h_Q), 
        [self.lstm_q_w_ih, # KNet weights
            self.lstm_q_b_ih,
            self.lstm_q_w_hh,
            self.lstm_q_b_hh],
        cm_weights=[self.lstm_q_ih_gain, # CM weights
            self.lstm_q_ih_shift])
        
        # FC 6
        in_FC6 = fw_update_diff
        out_FC6 = self.activation_func(F.linear(in_FC6, self.fc6_w, bias=self.fc6_b))

        # Sigma_lstm
        in_Sigma = torch.cat((self.out_Q, out_FC6), 2)           
        self.out_Sigma, self.h_Sigma = self.lstm_rnn_step(in_Sigma, (self.out_Sigma, self.h_Sigma), 
        [self.lstm_sigma_w_ih, # KNet weights
            self.lstm_sigma_b_ih,
            self.lstm_sigma_w_hh,
            self.lstm_sigma_b_hh],
        cm_weights=[self.lstm_sigma_ih_gain, # CM weights
            self.lstm_sigma_ih_shift])
        
        # FC 1
        in_FC1 = self.out_Sigma
        out_FC1 = self.activation_func(F.linear(in_FC1, self.fc1_w, bias=self.fc1_b))

        # FC 7
        in_FC7 = torch.cat((obs_diff, obs_innov_diff), 2)
        out_FC7 = self.activation_func(F.linear(in_FC7, self.fc7_w, bias=self.fc7_b))

        # S-lstm
        in_S = torch.cat((out_FC1, out_FC7), 2)
        self.out_S, self.h_S = self.lstm_rnn_step(in_S, (self.out_S, self.h_S), 
        [self.lstm_s_w_ih, # KNet weights
            self.lstm_s_b_ih,
            self.lstm_s_w_hh,
            self.lstm_s_b_hh],
        cm_weights=[self.lstm_s_ih_gain, # CM weights
            self.lstm_s_ih_shift])

        # FC 2
        in_FC2 = torch.cat((self.out_Sigma, self.out_S), 2)
        out_FC2 = self.activation_func(F.linear(in_FC2, self.fc2_w1, bias=self.fc2_b1))
        out_FC2 = F.linear(out_FC2, self.fc2_w2, bias=self.fc2_b2)

        #####################
        ### Backward Flow ###
        #####################
        # FC 3
        in_FC3 = torch.cat((self.out_S, out_FC2), 2)
        out_FC3 = self.activation_func(F.linear(in_FC3, self.fc3_w, bias=self.fc3_b))

        # FC 4
        in_FC4 = torch.cat((self.out_Sigma, out_FC3), 2)
        out_FC4 = self.activation_func(F.linear(in_FC4, self.fc4_w, bias=self.fc4_b))

        # updating hidden state of the Sigma-lstm
        self.h_Sigma = out_FC4
            
        return out_FC2
    ###############
    ### Forward ###
    ###############
    def forward(self, y, weights_knet = None, weights_cm = None):
        y = y.to(self.device)

        # case: hypernet generate CM weights
        if weights_cm is not None:
            assert(weights_knet is None) # KNet is initialized with frozen weights
            assert(self.use_context_mod == True) # if CM weights are provided, then CM should be used            
            self.split_cm_weights(weights_cm)

        # case: hypernet only generate KNet weights
        elif weights_knet is not None: 
            assert(self.knet_trainable == False) # if KNet weights are provided, then KNet should not be trainable
            weights_knet = weights_knet.to(self.device)
            self.split_weights(weights_knet)
        
        # case: KNet weights are trainable
        else:
            assert(self.knet_trainable == True) 

        return self.KNet_step(y)

    #########################
    ### Init Hidden State ###
    #########################
    def init_hidden(self):
        self.out_Q = self.prior_Q.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)
        self.out_Sigma = self.prior_Sigma.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)
        self.out_S = self.prior_S.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)

        self.h_S = torch.zeros(self.seq_len_input,self.batch_size,self.n ** 2).to(self.device) # batch size expansion   
        self.h_Sigma = torch.zeros(self.seq_len_input,self.batch_size,self.m ** 2).to(self.device) # batch size expansion
        self.h_Q = torch.zeros(self.seq_len_input,self.batch_size,self.m ** 2).to(self.device) # batch size expansion

    #####################
    ### Split weights ###
    #####################
    def split_weights(self, weights):
        """
        input: weights torch.tensor [total number of weights]
        """
        weight_index = 0
        # split weights and biases for FC 1 - 7
        def split_and_reshape_fc(weights, weight_index, shape_w, shape_b):
            length_w = shape_w[0] * shape_w[1]
            length_b = shape_b[0]
            fc_w = weights[weight_index:weight_index+length_w].reshape(shape_w[0], shape_w[1])
            weight_index = weight_index + length_w
            fc_b = weights[weight_index:weight_index+length_b].reshape(shape_b[0])
            weight_index = weight_index + length_b
            return fc_w, fc_b, weight_index
        
        self.fc1_w, self.fc1_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc1_w'], self.fc_shape['fc1_b'])
        self.fc2_w1, self.fc2_b1, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc2_w1'], self.fc_shape['fc2_b1'])
        self.fc2_w2, self.fc2_b2, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc2_w2'], self.fc_shape['fc2_b2'])
        self.fc3_w, self.fc3_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc3_w'], self.fc_shape['fc3_b'])
        self.fc4_w, self.fc4_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc4_w'], self.fc_shape['fc4_b'])
        self.fc5_w, self.fc5_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc5_w'], self.fc_shape['fc5_b'])
        self.fc6_w, self.fc6_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc6_w'], self.fc_shape['fc6_b'])
        self.fc7_w, self.fc7_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc7_w'], self.fc_shape['fc7_b'])

        # split weights and biases for lstm Q, Sigma, S
        def split_and_reshape_lstm(weights, weight_index, shape_w_ih, shape_b_ih, shape_w_hh, shape_b_hh):
            length_w_ih = shape_w_ih[0] * shape_w_ih[1]
            length_b_ih = shape_b_ih[0]
            length_w_hh = shape_w_hh[0] * shape_w_hh[1]
            length_b_hh = shape_b_hh[0]
            lstm_w_ih = weights[weight_index:weight_index+length_w_ih].reshape(shape_w_ih[0], shape_w_ih[1])
            weight_index = weight_index + length_w_ih
            lstm_b_ih = weights[weight_index:weight_index+length_b_ih].reshape(shape_b_ih[0])
            weight_index = weight_index + length_b_ih
            lstm_w_hh = weights[weight_index:weight_index+length_w_hh].reshape(shape_w_hh[0], shape_w_hh[1])
            weight_index = weight_index + length_w_hh
            lstm_b_hh = weights[weight_index:weight_index+length_b_hh].reshape(shape_b_hh[0])
            weight_index = weight_index + length_b_hh
            return lstm_w_ih, lstm_b_ih, lstm_w_hh, lstm_b_hh, weight_index
        
        self.lstm_q_w_ih, self.lstm_q_b_ih, self.lstm_q_w_hh, self.lstm_q_b_hh, weight_index = split_and_reshape_lstm(weights, weight_index, self.lstm_shape['lstm_q_w_ih'], self.lstm_shape['lstm_q_b_ih'], self.lstm_shape['lstm_q_w_hh'], self.lstm_shape['lstm_q_b_hh'])
        self.lstm_sigma_w_ih, self.lstm_sigma_b_ih, self.lstm_sigma_w_hh, self.lstm_sigma_b_hh, weight_index = split_and_reshape_lstm(weights, weight_index, self.lstm_shape['lstm_sigma_w_ih'], self.lstm_shape['lstm_sigma_b_ih'], self.lstm_shape['lstm_sigma_w_hh'], self.lstm_shape['lstm_sigma_b_hh'])
        self.lstm_s_w_ih, self.lstm_s_b_ih, self.lstm_s_w_hh, self.lstm_s_b_hh, weight_index = split_and_reshape_lstm(weights, weight_index, self.lstm_shape['lstm_s_w_ih'], self.lstm_shape['lstm_s_b_ih'], self.lstm_shape['lstm_s_w_hh'], self.lstm_shape['lstm_s_b_hh'])

    def split_cm_weights(self, weights):   
        self.lstm_q_ih_gain = weights[0].to(self.device)
        self.lstm_q_ih_shift = weights[1].to(self.device)
       
        self.lstm_sigma_ih_gain = weights[2].to(self.device)
        self.lstm_sigma_ih_shift = weights[3].to(self.device)
        
        self.lstm_s_ih_gain = weights[4].to(self.device)
        self.lstm_s_ih_shift = weights[5].to(self.device)

    ########################
    ### LSTM computation ###
    ########################    
    def lstm_rnn_step(self, x_t, h_t, lstm_weights, cm_weights=None):
        """
        Args:
            x_t: Tensor of size ``[1, batch_size, n_inputs]`` with inputs.
            h_t (tuple): (y_t, c_t) Tuple of length 2, containing two tensors of size
                ``[batch_size, n_hidden]`` with previous output y and c.
            lstm_weights: List of length 4, containing weights and biases for
                the LSTM layer.
            cm_weights: List of length 2, containing gains and shifts for
                the LSTM layer.
           
        Returns:
            - **y_t** (torch.Tensor): The tensor ``y_t`` of size
              ``[1, batch_size, n_hidden]`` with the output state.
            - **c_t** (torch.Tensor): The tensor ``c_t`` of size
              ``[1, batch_size, n_hidden]`` with the new cell state.
        """

        c_t = h_t[1]
        y_t = h_t[0]

        assert len(lstm_weights) == 4
        weight_ih = lstm_weights[0]
        bias_ih = lstm_weights[1]
        weight_hh = lstm_weights[2]
        bias_hh = lstm_weights[3]

        if cm_weights is not None:
            assert(self.use_context_mod == True) # make sure context mod is enabled
            assert len(cm_weights) == 2
            cm_ih_gain = cm_weights[0]
            cm_ih_shift = cm_weights[1]

        d_hidden = weight_hh.shape[1]

        # Compute total pre-activation input.
        if cm_weights is not None:
            gates_ih = x_t @ weight_ih.t() + bias_ih
            gates_hh = y_t @ weight_hh.t() + bias_hh
            # apply context modulation
            gates_ih = gates_ih.mul(cm_ih_gain) + cm_ih_shift
            gates = gates_ih + gates_hh
        else:    
            gates = x_t @ weight_ih.t() + y_t @ weight_hh.t()
            gates = gates + bias_ih + bias_hh

        i_t = gates[:, :, :d_hidden]
        f_t = gates[:, :, d_hidden:d_hidden*2]
        g_t = gates[:, :, d_hidden*2:d_hidden*3]
        o_t = gates[:, :, d_hidden*3:]

        # Compute activation.
        i_t = torch.sigmoid(i_t) # input
        f_t = torch.sigmoid(f_t) # forget
        g_t = torch.tanh(g_t)
        o_t = torch.sigmoid(o_t) # output

        # Compute c states.
        c_t = f_t * c_t + i_t * g_t

        # Compute h states.
        y_t = o_t * torch.tanh(c_t)
        
        return y_t, c_t
    