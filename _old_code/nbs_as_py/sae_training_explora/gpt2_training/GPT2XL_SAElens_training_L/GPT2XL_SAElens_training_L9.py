#!/usr/bin/env python
# coding: utf-8

# ## Setup

# In[1]:


get_ipython().run_cell_magic('capture', '', 'try:\n    import google.colab # type: ignore\n    from google.colab import output\n    %pip install sae-lens==1.3.0 transformer-lens==1.17.0 circuitsvis==1.43.2\nexcept:\n    from IPython import get_ipython # type: ignore\n    ipython = get_ipython(); assert ipython is not None\n    ipython.run_line_magic("load_ext", "autoreload")\n    ipython.run_line_magic("autoreload", "2")\n')


# In[2]:


import torch
import os

from sae_lens.training.config import LanguageModelSAERunnerConfig
from sae_lens.training.lm_runner import language_model_sae_runner

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    device = "mps"
else:
    device = "cpu"

print("Using device:", device)
os.environ["TOKENIZERS_PARALLELISM"] = "false"


# # Model Selection and Evaluation
# 

# In[3]:


from transformer_lens import HookedTransformer

model = HookedTransformer.from_pretrained(
    "gpt2-xl"
)  # This will wrap huggingface models and has lots of nice utilities.


# ### Getting a vibe for a model using `model.generate`

# Let's start by generating some stories using the model.

# In[4]:


# here we use generate to get 10 completeions with temperature 1. Feel free to play with the prompt to make it more interesting.
for i in range(2):
    display(
        model.generate(
            # "I think you're",
            "You messed up because you're",
            stop_at_eos=False,  # avoids a bug on MPS
            temperature=1,
            verbose=False,
            max_new_tokens=50,
        )
    )


# One thing we notice is that the model seems to be able to repeat [X] consistently. To better understand the models ability to remember [X], let's extract a prompt where the next character is determined and use the "test_prompt" utility from TransformerLens to check the ranking of the token for [X].

# ### Spot checking model abilities with `transformer_lens.utils.test_prompt`

# In[5]:


from transformer_lens.utils import test_prompt

# Test the model with a prompt
test_prompt(
    "I think you're",
    " angry",
    model,
    prepend_space_to_answer=False,
)


# In the output above, we see that the model assigns ~ % probability to [X] being the next token.

# ### Exploring Model Capabilities with Log Probs

# Look at token log probs for ALL tokens in a prompt. Hover to get the top5 tokens by log probability. Darker tokens are tokens where the model assigned a higher probability to the actual next token.
# 
# Given prompt "A B C D", this predicts the rank of predicting "C" given "A B". The actual prompt has "A B C", but if only "A B" was given, how "much" does the model expect C? [improve this explanation]

# In[6]:


import circuitsvis as cv  # optional dep, install with pip install circuitsvis

# Let's make a longer prompt and see the log probabilities of the tokens
example_prompt = """Hi, how are you doing this? I'm really enjoying your posts"""
logits, cache = model.run_with_cache(example_prompt)
cv.logits.token_log_probs(
    model.to_tokens(example_prompt),
    model(example_prompt)[0].log_softmax(dim=-1),
    model.to_string,
)
# hover on the output to see the result.


# Let's combine `model.generate` and the token log probs visualization to see the log probs on text generated by the model. Note that we can play with the temperature and this should sample less likely trajectories according to the model.
# 
# Some things to explore:
# - Which tokens does the model assign high probability to? Can you see how the model should know which word comes next?
# - What happens if you increase / decrease the temperature?
# - Do the rankings of tokens seem sensible to you? What about where the model doesn't assign a high probability to the token which came next?

# In[7]:


example_prompt = model.generate(
    "You messed up because you're",
    stop_at_eos=False,  # avoids a bug on MPS
    temperature=1,
    verbose=True,
    max_new_tokens=50,
)
logits, cache = model.run_with_cache(example_prompt)
cv.logits.token_log_probs(
    model.to_tokens(example_prompt),
    model(example_prompt)[0].log_softmax(dim=-1),
    model.to_string,
)


# # Training an SAE
# 
# Now we're ready to train out SAE. We'll make a runner config, instantiate the runner and the rest is taken care of for us!
# 
# During training, you use weights and biases to check key metrics which indicate how well we are able to optimize the variables we care about.
# 
# To get a better sense of which variables to look at, you can read my (Joseph's) post [here](https://www.lesswrong.com/posts/f9EgfLSurAiqRJySD/open-source-sparse-autoencoders-for-all-residual-stream) and especially look at my weights and biases report [here](https://links-cdn.wandb.ai/wandb-public-images/links/jbloom/uue9i416.html).
# 
# A few tips:
# - Feel free to reorganize your wandb dashboard to put L0, CE_Loss_score, explained variance and other key metrics in one section at the top.
# - Make a [run comparer](https://docs.wandb.ai/guides/app/features/panels/run-comparer) when tuning hyperparameters.
# - You can download the resulting sparse autoencoder / sparsity estimate from wandb and upload them to huggingface if you want to share your SAE with other.
#     - cfg.json (training config)
#     - sae_weight.safetensors (model weights)
#     - sparsity.safetensors (sparsity estimate)

# ## MLP Out
# 
# I've tuned the hyperparameters below for a decent SAE which achieves 86% CE Loss recovered and an L0 of ~85, and runs in about 2 hours on an M3 Max. You can get an SAE that looks better faster if you only consider L0 and CE loss but it will likely have more dense features and more dead features. Here's a link to my output with two runs with two different L1's: https://wandb.ai/jbloom/sae_lens_tutorial .

# Paste wandb API key below

# In[8]:


# total_training_steps = 30_000  # probably we should do more
total_training_steps = 1000  # probably we should do more
batch_size = 4096
# batch_size = 4
total_training_tokens = total_training_steps * batch_size

lr_warm_up_steps = 0
lr_decay_steps = total_training_steps // 5  # 20% of training
l1_warm_up_steps = total_training_steps // 20  # 5% of training

cfg = LanguageModelSAERunnerConfig(
    # Data Generating Function (Model + Training Distibuion)
    model_name="gpt2-xl",  # our model (more options here: https://neelnanda-io.github.io/TransformerLens/generated/model_properties_table.html)
    # hook_point="blocks.20.hook_mlp_out",  # A valid hook point (see more details here: https://neelnanda-io.github.io/TransformerLens/generated/demos/Main_Demo.html#Hook-Points)
    # hook_point_layer=20,  # Only one layer in the model.
    hook_point="blocks.9.hook_mlp_out",  # A valid hook point (see more details here: https://neelnanda-io.github.io/TransformerLens/generated/demos/Main_Demo.html#Hook-Points)
    hook_point_layer=9,  # Only one layer in the model.
    # d_in=1024,  # the width of the mlp output.
    d_in=1600,  # the width of the mlp output.
    # dataset_path="apollo-research/roneneldan-TinyStories-tokenizer-gpt2",  # this is a tokenized language dataset on Huggingface for the Tiny Stories corpus.
    dataset_path="stas/openwebtext-10k",
    is_dataset_tokenized=True,
    # streaming=True,  # we could pre-download the token dataset if it was small.

    # SAE Parameters
    mse_loss_normalization=None,  # We won't normalize the mse loss,
    expansion_factor=16,  # the width of the SAE. Larger will result in better stats but slower training.
    b_dec_init_method="zeros",  # The geometric median can be used to initialize the decoder weights.
    apply_b_dec_to_input=False,  # We won't apply the decoder weights to the input.
    normalize_sae_decoder=False,
    # scale_sparsity_penalty_by_decoder_norm=True,
    # decoder_heuristic_init=True,
    # init_encoder_as_decoder_transpose=True,
    # normalize_activations=False,

    # Training Parameters
    lr=5e-5,  # lower the better, we'll go fairly high to speed up the tutorial.
    adam_beta1=0.9,  # adam params (default, but once upon a time we experimented with these.)
    adam_beta2=0.999,
    lr_scheduler_name="constant",  # constant learning rate with warmup. Could be better schedules out there.
    lr_warm_up_steps=lr_warm_up_steps,  # this can help avoid too many dead features initially.
    lr_decay_steps=lr_decay_steps,  # this will help us avoid overfitting.
    l1_coefficient=5,  # will control how sparse the feature activations are
    # l1_warm_up_steps=l1_warm_up_steps,  # this can help avoid too many dead features initially.
    lp_norm=1.0,  # the L1 penalty (and not a Lp for p < 1)
    # train_batch_size_tokens=batch_size,
    context_size=256,  # will control the lenght of the prompts we feed to the model. Larger is better but slower. so for the tutorial we'll use a short one.
    # Activation Store Parameters
    n_batches_in_buffer=64,  # controls how many activations we store / shuffle.
    training_tokens=total_training_tokens,  # 100 million tokens is quite a few, but we want to see good stats. Get a coffee, come back.
    # store_batch_size_prompts=16,

    # Resampling protocol
    use_ghost_grads=False,  # we don't use ghost grads anymore.
    feature_sampling_window=1000,  # this controls our reporting of feature sparsity stats
    dead_feature_window=1000,  # would effect resampling or ghost grads if we were using it.
    dead_feature_threshold=1e-4,  # would effect resampling or ghost grads if we were using it.

    # WANDB
    log_to_wandb=True,  # always use wandb unless you are just testing code.
    # log_to_wandb=False,
    wandb_project="sae_lens_exploraTest_L9",
    # wandb_project="sae_lens_tutorial",
    wandb_log_frequency=30,
    # eval_every_n_wandb_logs=20,

    # Misc
    device=device,
    seed=42,
    n_checkpoints=0,
    checkpoint_path="checkpoints",
    dtype=torch.float32,
)

# look at the next cell to see some instruction for what to do while this is running.
sparse_autoencoder_dictionary = language_model_sae_runner(cfg)


# # Interpret SAE
# 

# In[ ]:


import pandas as pd

# Let's start by getting the top 10 logits for each feature

sparse_autoencoder = next(iter(sparse_autoencoder_dictionary))[1]
projection_onto_unembed = sparse_autoencoder.W_dec @ model.W_U


# get the top 10 logits.
vals, inds = torch.topk(projection_onto_unembed, 10, dim=1)

# get 10 random features
random_indices = torch.randint(0, projection_onto_unembed.shape[0], (10,))

# Show the top 10 logits promoted by those features
top_10_logits_df = pd.DataFrame(
    [model.to_str_tokens(i) for i in inds[random_indices]],
    index=random_indices.tolist(),
).T
top_10_logits_df

