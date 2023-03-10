#!/usr/bin/env python
import os
import numpy as np
import pandas as pd
import torch

from skimage.util import random_noise
from skimage.transform import rotate
import elasticdeform
from gbm_project.data_prep import retrieve_data

class DatasetGenerator(torch.utils.data.Dataset):
    """
    generate images for pytorch dataset
    """
    def __init__(self, data_indices, labels, data_dir='../../data/upenn_GBM/images/NIfTI-files/', csv_dir='../../data/upenn_GBM/csvs/radiomic_features_CaPTk/', modality=['FLAIR'], dim=(70,86,86), n_channels=3, to_augment=False, to_encode=False, to_sectionate=False, augment_types=('noise', 'flip', 'rotate', 'deform'), seed=42, transform=None, target_transform=None):
        self.labels = labels
        self.data_indices = data_indices

        if len(modality) > 1:
            self.n_channels = len(modality)
        else:
            self.n_channels = n_channels
        self.dim = dim
        self.rng_noise = np.random.default_rng(seed)
        self.rng_rotate = np.random.default_rng(seed)
        self.data_dir = data_dir
        self.csv_dir = csv_dir
        self.modality = modality
        self.to_augment = to_augment
        self.augment_types = augment_types
        self.to_encode = to_encode
        self.radiomics = None
        self.to_sectionate = to_sectionate
        self.transform = transform
        self.target_transform = target_transform

        if self.to_encode:
            if self.modality[0] == 'mod':
                modality_tmp = 'FLAIR'
            else:
                modality_tmp = self.modality[0]
            temp_radiomics = retrieve_data(self.csv_dir, modality_tmp)[0]
            self.radiomics = temp_radiomics.loc[self.data_indices, 2:]
            scaler = StandardScaler()

            temp_index = self.radiomics.index.tolist()
            temp_column = self.radiomics.columns.tolist()
            temp_scaled = scaler.fit_transform(self.radiomics)

            self.radiomics = pd.DataFrame(temp_scaled, columns=temp_column, index=temp_index)
        

        if self.to_augment:
            augment_idx = {}
            for aug in self.augment_types:
                augment_idx[aug] = self.labels.copy(deep=True)
                augment_idx[aug].index = augment_idx[aug].index+'_'+aug

                self.data_indices = self.data_indices.append(augment_idx[aug].index)

            self.labels = pd.concat([self.labels, *augment_idx.values()])


    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx=idx.tolist()

        pat_idx = self.labels.index.values[idx]
        label = self.labels.loc[pat_idx]

        # retrieve array (image) based on number of modalities
        if len(self.modality) < 2:
            in_arr = self.get_pat_array(pat_idx)
        else:
            in_arr = self.get_pat_mod_array(pat_idx)

        if self.to_sectionate:
            in_arr = np.reshape(in_arr, self.dim)

        if self.transform:
            in_arr = self.transform(in_arr)
        if self.target_transform:
            label = self.target_transform(label)
        if self.n_channels == 1:
            in_arr = np.expand_dims(in_arr, axis=0)

        return torch.from_numpy(in_arr), torch.tensor(label)


    def get_pat_array(self, pat_idx):
        '''
        retrieve image array if only one modality is in use
        applies transformations as necessary
        '''
        
        if self.to_augment:
            if len(pat_idx.split('_')) == 3:
                in_arr = np.load(os.path.join(self.data_dir,'_'.join(pat_idx.split('_')[:2])+'_'+self.modality[0]+ '.npy'))
            else:
                in_arr = np.load(os.path.join(self.data_dir,pat_idx+'_'+self.modality[0]+ '.npy'))
        else:
            in_arr = np.load(os.path.join(self.data_dir,pat_idx+'_'+self.modality[0]+ '.npy'))

        # take the first three images such that they are arranged into a single image with 3 channels.`
        if self.n_channels > 1:
            if self.n_channels == 5:
                in_arr = in_arr
            elif self.n_channels == 4:
                in_arr = in_arr[[0,1,2,4]]
            else:
                in_arr = in_arr[0:3]
        else:
            in_arr = in_arr[3]

        aug = pat_idx.split('_')[-1]
        if aug in self.augment_types:
            if 'flip' in aug:
                in_arr = self.apply_flip(in_arr)
            if 'rotate' in aug:
                in_arr = self.apply_rotation(in_arr)
            if 'noise' in aug:
                in_arr = self.apply_noise(in_arr)
            if 'deform' in aug:
                in_arr = self.apply_deformation(in_arr)

        if self.to_sectionate:
            if self.n_channels == 1:
                in_arr = np.reshape(in_arr, self.dim)
            else:
                in_arr = np.reshape(in_arr, (*self.dim, self.n_channels))

        return in_arr


    def get_pat_mod_array(self, pat_idx):
        '''
        retrieve image array if multiple modalities are in use.
        Channels will then be modality based instead of tumor section based
        applies transformations as necessary
        '''

        mod_arr = np.empty((len(self.modality), *self.dim))

        for imod, mod in enumerate(self.modality):
            if self.to_augment:
                if len(pat_idx.split('_')) == 3:
                    in_arr = np.load(os.path.join(self.data_dir,'_'.join(pat_idx.split('_')[:2])+'_'+mod+ '.npy'))
                else:
                    in_arr = np.load(os.path.join(self.data_dir,pat_idx+'_'+mod+ '.npy'))
            else:
                in_arr = np.load(os.path.join(self.data_dir,pat_idx+'_'+mod+ '.npy'))

            # take the whole tumor image, since each channel will be a separate modality
            mod_arr[imod] = in_arr[3]

        aug = pat_idx.split('_')[-1]
        if aug in self.augment_types:
            if 'flip' in aug:
                mod_arr = self.apply_flip(mod_arr)
            if 'rotate' in aug:
                mod_arr = self.apply_rotation(mod_arr)
            if 'noise' in aug:
                mod_arr = self.apply_noise(mod_arr)
            if 'deform' in aug:
                mod_arr = self.apply_deformation(mod_arr)

        return mod_arr



    def apply_noise(self, arr):
        return random_noise(arr, mode='gaussian', seed=self.rng_noise)


    def apply_rotation(self, arr):
        angle = self.rng_rotate.integers(-180, high=180)
        if self.n_channels==1:
            arr = rotate(arr, angle, preserve_range=True)
        elif self.n_channels==4:
            arr[0,:,:,:] = rotate(arr[0,:,:,:], angle, preserve_range=True)
            arr[1,:,:,:] = rotate(arr[1,:,:,:], angle, preserve_range=True)
            arr[2,:,:,:] = rotate(arr[2,:,:,:], angle, preserve_range=True)
            arr[3,:,:,:] = rotate(arr[3,:,:,:], angle, preserve_range=True)
        elif self.n_channels==5:
            arr[0,:,:,:] = rotate(arr[0,:,:,:], angle, preserve_range=True)
            arr[1,:,:,:] = rotate(arr[1,:,:,:], angle, preserve_range=True)
            arr[2,:,:,:] = rotate(arr[2,:,:,:], angle, preserve_range=True)
            arr[3,:,:,:] = rotate(arr[3,:,:,:], angle, preserve_range=True)
            arr[4,:,:,:] = rotate(arr[4,:,:,:], angle, preserve_range=True)
        else:
            arr[0,:,:,:] = rotate(arr[0,:,:,:], angle, preserve_range=True)
            arr[1,:,:,:] = rotate(arr[1,:,:,:], angle, preserve_range=True)
            arr[2,:,:,:] = rotate(arr[2,:,:,:], angle, preserve_range=True)
        return arr


    def apply_deformation(self, arr):
        if self.n_channels==1:
            arr = elasticdeform.deform_random_grid(arr, sigma=5, order=0, axis=(0,1,2))
        else:
            arr = elasticdeform.deform_random_grid(arr, sigma=5, order=0, axis=(1,2,3))
        return arr


    def apply_flip(self, arr):
        if self.n_channels==1:
            arr = np.flip(arr, axis=(0,1,2)).copy()
        else:
            arr = np.flip(arr, axis=(1,2,3)).copy()
        return arr


    def encode(self, arr, idx):
        arr.append(np.zeros((self.n_channels, self.dim[1], self.dim[2])), axis=0)

        ed_arr = self.radiomics.loc[idx].iloc[:, self.radiomics.columns.str.contains('_ED_', regex=False)] 
        et_arr = self.radiomics.loc[idx].iloc[:, self.radiomics.columns.str.contains('_ET_', regex=False)] 
        nc_arr = self.radiomics.loc[idx].iloc[:, self.radiomics.columns.str.contains('_NC_', regex=False)] 

        ed_arr = ed_arr.reshape((15,9))
        et_arr = et_arr.reshape((15,9))
        nc_arr = nc_arr.reshape((15,9))

        arr[0, -1, (43-7):(41+8), (41-4):(41+5)] = ed_arr
        arr[1, -1, (43-7):(41+8), (41-4):(41+5)] = et_arr
        arr[2, -1, (43-7):(41+8), (41-4):(41+5)] = nc_arr

        return arr
