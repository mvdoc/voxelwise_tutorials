"""
=======================================
Fit a ridge model with wordnet features
=======================================

In this first example, we model the fMRI responses with semantic labeling
"wordnet" features manually annotated to the movie stimulus, using a
regularized linear regression model.

We first concatenate the features with multiple delays, to account for the
hemodynamic response. The linear regression model will then weight each delayed
feature with a different weight, to build a predictive model.

The linear regression is regularized to improve robustness to correlated
features and to improve generalization. The optimal regularization
hyperparameter is selected over a grid-search with cross-validation.

Finally, the model generalization performance is evaluated on a held-out test
set, comparing the model predictions with the corresponding ground-truth fMRI
responses.

The ridge model is fitted with the package
`himalaya <https://github.com/gallantlab/himalaya>`_.
"""
###############################################################################

# path of the data directory
directory = '/data1/tutorials/vim-4/'

# modify to use another subject
subject = "S01"

###############################################################################
# Load the data
# -------------
#
# We first load the fMRI responses.
import os.path as op
import numpy as np

from voxelwise.io import load_hdf5_array

file_name = op.join(directory, "responses", f"{subject}_responses.hdf")
Y_train = load_hdf5_array(file_name, key="Y_train")
Y_test = load_hdf5_array(file_name, key="Y_test")
run_onsets = load_hdf5_array(file_name, key="run_onsets")

# Average test repeats, since we cannot model the non-repeatable part of
# fMRI responses.
Y_test = Y_test.mean(0)

# remove nans, mainly present on non-cortical voxels
Y_train = np.nan_to_num(Y_train)
Y_test = np.nan_to_num(Y_test)

###############################################################################
# Then, we load the semantic labeling "wordnet" features, that are going to be
# used for the linear regression model.

feature_space = "wordnet"
file_name = op.join(directory, "features", f"{feature_space}.hdf")
X_train = load_hdf5_array(file_name, key="X_train")
X_test = load_hdf5_array(file_name, key="X_test")

# We use single precision float to speed up model fitting on GPU.
X_train = X_train.astype("float32")
X_test = X_test.astype("float32")

###############################################################################
# Define the cross-validation scheme
# ----------------------------------
#
# To select the best hyperparameter through cross-validation, we must define a
# train-validation splitting scheme. Since fMRI time-series are autocorrelated
# in time, we should preserve as much as possible the time blocks.
# In other words, since consecutive time samples are correlated, we should not
# put one time sample in the training set and the immediately following time
# sample in the validation set. Thus, we define here a leave-one-run-out
# cross-validation split, which preserves each recording run.

from sklearn.model_selection import check_cv
from voxelwise.utils import generate_leave_one_run_out

n_samples_train = X_train.shape[0]

# indice of first sample of each run, each run having 600 samples
run_onsets = np.arange(0, n_samples_train, 600)

# define a cross-validation splitter, compatible with scikit-learn
cv = generate_leave_one_run_out(n_samples_train, run_onsets)
cv = check_cv(cv)  # copy the splitter into a reusable list

###############################################################################
# Define the model
# ----------------
#
# Now, let's define the model pipeline.
#
# We first center the features, since we will not use an intercept.
from sklearn.preprocessing import StandardScaler

scaler = StandardScaler(with_mean=True, with_std=False)

###############################################################################
# Then we concatenate the features with multiple delays, to account for the
# hemodynamic response. The linear regression model will then weight each
# delayed feature with a different weight, to build a predictive model.
#
# With a sample every 2 seconds, we use 4 delays [1, 2, 3, 4] to cover the
# most part of the hemodynamic response peak.
from voxelwise.delayer import Delayer

delayer = Delayer(delays=[1, 2, 3, 4])

###############################################################################
# The model we use is a ridge regression. When the number of features is
# larger than the number of samples, it is more efficient to solve a ridge
# regression using the (equivalent) dual formulation, kernel ridge regression
# with a linear kernel.
# Here, we have 3600 training samples, and 1705 * 4 = 6820 features (we
# multiply by 4 since we use 4 time delays), therefore we use kernel ridge
# regression.

# With one target, we could directly use the pipeline in scikit-learn's
# GridSearchCV, to select the optimal hyperparameters over cross-validation.
# However, GridSearchCV can only optimize one score. Thus, in the multiple
# target case, GridSearchCV can only optimize e.g. the mean score over targets.
# Here, we want to find a different optimal hyperparameter per target/voxel, so
# we use himalaya's KernelRidgeCV instead.
from himalaya.kernel_ridge import KernelRidgeCV

###############################################################################
# The scale of the regularization hyperparameter alpha is unknown, so we use
# a large logarithmic range, and we will check after the fit that best
# hyperparameters are not all on one range edge.
alphas = np.logspace(1, 20, 20)

kernel_ridge_cv = KernelRidgeCV(
    alphas=alphas, cv=cv,
    solver_params=dict(n_targets_batch=500, n_alphas_batch=5,
                       n_targets_batch_refit=100))

###############################################################################
# We set himalaya's backend to "torch_cuda" to fit the model using GPU.
# The available backends are:
#
# - "numpy" (CPU) (default)
# - "torch" (CPU)
# - "torch_cuda" (GPU)
# - "cupy" (GPU)
from himalaya.backend import set_backend
backend = set_backend("torch_cuda")

###############################################################################
# We use scikit-learn Pipeline to link the different steps together.
# The scikit-learn Pipeline can be used as a regular estimator, calling
# `pipeline.fit`, `pipeline.predict`, etc.
# Using a pipeline can be useful to clarify the different steps, avoid
# cross-validation mistakes, or automatically cache intermediate results.
from sklearn.pipeline import make_pipeline

# define the pipeline
pipeline = make_pipeline(
    scaler,
    delayer,
    kernel_ridge_cv,
)

###############################################################################
# We can display the scikit-learn pipeline with an HTML diagram.
from sklearn import set_config
set_config(display='diagram')

pipeline

###############################################################################
# Fit the model
# -------------
#
# We fit on the train set, and score on the test set.
# Here the scores are the R^2 scores, with values in ]-inf, 1].
# A value of 1 means the predictions are perfect.

pipeline.fit(X_train, Y_train)

scores = pipeline.score(X_test, Y_test)

###############################################################################
# Since we performed the KernelRidgeCV on GPU, scores are returned as
# torch.Tensor on GPU. Thus, we need to move them into numpy arrays on CPU, to
# be able to use them e.g. in a matplotlib figure.
scores = backend.to_numpy(scores)

###############################################################################
# Plot the model performances
# ---------------------------
#
# To visualize the model performances, we can plot them on a flatten
# surface of the brain, using a mapper that is specific to the subject brain.
import matplotlib.pyplot as plt
from voxelwise.viz import plot_flatmap_from_mapper

mapper_file = op.join(directory, "mappers", f"{subject}_mappers.hdf")
ax = plot_flatmap_from_mapper(scores, mapper_file, vmin=0, vmax=0.4)
plt.show()

###############################################################################
# Another possible visualization is to map the voxel data to a Freesurfer
# average surface ("fsaverage").

import cortex
from voxelwise.io import load_hdf5_sparse_array

surface = "fsaverage_pycortex"  # ("fsaverage" outside the Gallant lab)

# First, let's download the fsaverage surface if it does not exist
if not hasattr(cortex.db, surface):
    cortex.utils.download_subject(subject_id=surface)

# Then, we use load the fsaverage mappers, and use it with a dot product
voxel_to_fsaverage = load_hdf5_sparse_array(mapper_file, 'voxel_to_fsaverage')
projected = voxel_to_fsaverage @ scores

# Finally, we use the data projected on a surface, using pycortex
vertex = cortex.Vertex(projected, surface, vmin=0, vmax=0.4, cmap='inferno',
                       with_curvature=True)
fig = cortex.quickshow(vertex)
plt.show()

# Alternatively, we can start a webGL viewer in the browser, to visualize the
# surface in 3D. Note that this cannot be executed on sphinx gallery.
if False:
    cortex.webshow(vertex, open_browser=True)

###############################################################################
# Since the scale of alphas is unknown, we plot the optimal alphas selected by
# the solver over cross-validation. This plot is helpful to refine the alpha
# grid if the range is too small or too large.
#
# Note that some voxels are at the maximum regularization of the grid. These
# are voxels where the model has no predictive power, and where the optimal
# regularization is large to lead to a prediction equal to zero.

from himalaya.viz import plot_alphas_diagnostic

plot_alphas_diagnostic(best_alphas=backend.to_numpy(pipeline[-1].best_alphas_),
                       alphas=alphas)

plt.show()

###############################################################################
# Compare with a model without delays
# -----------------------------------
#
# To present an example of model comparison, we define here another model,
# without feature delays (i.e. no `Delayer`). This model is unlikely to perform
# well, since fMRI responses are delayed in time with respect to the stimulus.

pipeline_nodelay = make_pipeline(
    StandardScaler(with_mean=True, with_std=False),
    KernelRidgeCV(
        alphas=alphas, cv=cv,
        solver_params=dict(n_targets_batch=500, n_alphas_batch=5,
                           n_targets_batch_refit=100)),
)
pipeline

pipeline_nodelay.fit(X_train, Y_train)
scores_nodelay = pipeline_nodelay.score(X_test, Y_test)
scores_nodelay = backend.to_numpy(scores_nodelay)

###############################################################################
# Here we plot the comparison of model performances with a 2D histogram.
# All ~70k voxels are represented in this histogram, where the diagonal
# corresponds to identical performance for both models. A distibution deviating
# from the diagonal means that one model has better predictive performances
# than the other.

from voxelwise.viz import plot_hist2d

ax = plot_hist2d(scores_nodelay, scores)
ax.set(title='Generalization R2 scores', xlabel='model without delays',
       ylabel='model with delays')
plt.show()

###############################################################################
# Visualize the HRF
# -----------------
#
# Here we will visualize the hemodynamic response function (HRF), as captured
# in the ridge regression weights.
#
# Fitting a kernel ridge regression results in a set of coefficients called the
# "dual" coefficients. These coefficients are different from the "primal"
# coefficients obtained with a ridge regression, but the primal coefficients
# can be computed from the dual coefficients using the training features.
#
# To better visualize the HRF, we will refit a model with more delays, but only
# on a selection of voxels to speed up the computations.

# pick the 10 best voxels
voxel_selection = np.argsort(scores)[-10:]

# define a pipeline with more delays
pipeline_many_delays = make_pipeline(
    StandardScaler(with_mean=True, with_std=False),
    Delayer(delays=np.arange(7)),
    KernelRidgeCV(
        alphas=alphas, cv=cv,
        solver_params=dict(n_targets_batch=500, n_alphas_batch=5,
                           n_targets_batch_refit=100)),
)

pipeline_many_delays.fit(X_train, Y_train[:, voxel_selection])

# get the (primal) ridge regression coefficients
primal_coef = pipeline_many_delays[-1].get_primal_coef()
primal_coef = backend.to_numpy(primal_coef)

# get the delays
delays = pipeline_many_delays.named_steps['delayer'].delays
# split the ridge coefficients per delays
primal_coef_per_delay = np.stack(np.split(primal_coef, len(delays), axis=0))

# select the feature with the largest coefficients for each voxel
feature_selection = np.argmax(np.sum(np.abs(primal_coef_per_delay), axis=0),
                              axis=0)
primal_coef_selection = primal_coef_per_delay[:, feature_selection,
                                              np.arange(len(voxel_selection))]

plt.plot(delays, primal_coef_selection)
plt.xlabel('Delays')
plt.xticks(delays)
plt.ylabel('Ridge coefficients')
plt.title(f'Largest feature for the {len(voxel_selection)} best voxels')
plt.axhline(0, color='k', linewidth=0.5)
plt.show()

###############################################################################
# We see that the hemodynamic response function (HRF) is captured in the model
# weights. In practice, we can limit the number of features by using only
# the most informative delays, for example [1, 2, 3, 4].