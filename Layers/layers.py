
import tensorflow as tf
import numpy as np
from .util import shape_list,gelu,swish,act_fns


def Attention_scaling(qs, ks):
    rhs = tf.cumsum(ks, axis=0)
    return tf.einsum("lbhm,lbhm->lbh", qs, rhs)

"""
currently not well implemented
"""
def Attention_matrix(qs, ks, vs):
    rhs = tf.expand_dims(ks, axis=-2) * tf.expand_dims(vs, axis=-1)  # [L,B,H,D,M]
    rhs = tf.cumsum(rhs, axis=0)
    return tf.linalg.matvec(rhs, qs)
"""
-----------------------------------------------
"""


class norm(tf.keras.layers.Layer):
  def __init__(self, 
               axis = [-1],
               e = 1e-5
               ):
    super(norm, self).__init__()
    self.axis = axis
    self.e = e

  def build(self, x):
      n_state = x[-1]
      self.g = self.add_weight("g_n", shape = [n_state], initializer=tf.constant_initializer(1))
      self.b = self.add_weight("b_n", shape = [n_state], initializer=tf.constant_initializer(0))

  def call(self, x):
      u = tf.math.reduce_mean(x, axis = self.axis, keepdims=True)
      s = tf.math.reduce_mean(tf.square(x-u), axis = self.axis, keepdims=True)
      x = (x - u) * tf.math.rsqrt(s + self.e)
      return x*self.g + self.b


class mask(tf.keras.layers.Layer):
  def __init__(self):
    super(mask, self).__init__()
    self.b = None

  def build(self, input_shape):
      n = input_shape[-1]
      self.b = tf.linalg.band_part(tf.ones([n, n]), -1, 0)
      self.b = tf.reshape(self.b, [1, 1, n, n])

  def call(self, inputs):
    return inputs*self.b + -1e9*(1-self.b)


class embedding(tf.keras.layers.Layer):
    def __init__(
        self,
        n_vocab,
        n_special,
        n_ctx,
        n_embd,
        pdrop = .1,
        freeze_emb = False,
        train = True,
    ):
        super(embedding, self).__init__()
        self.train = train    
        self.n_vocab = n_vocab
        self.n_special = n_special
        self.n_ctx = n_ctx
        self.n_embd = n_embd
        self.pdrop = pdrop
        self.freeze_emb = freeze_emb


    def build(self, x):
        if self.freeze_emb:
            
            self.w_embed_f2 = self.add_weight("we_1",shape = [self.n_ctx, self.n_embd], 
                                     initializer = tf.random_normal_initializer(stddev=0.02, seed=None),
                                     trainable = False)


            self.w_embed_t = self.add_weight("we_2",shape = [self.n_special, self.n_embd], 
                                     initializer = tf.random_normal_initializer(stddev=0.02, seed=None),
                                     trainable = True)
            
            
            self.w_embed_f1 = self.add_weight("we_3",shape = [self.n_vocab, self.n_embd], 
                                     initializer = tf.random_normal_initializer(stddev=0.02, seed=None),
                                     trainable = False)
            
        else :
            self.w_embed = self.add_weight("we", 
                                     shape = [self.n_vocab+self.n_special+self.n_ctx, self.n_embd], 
                                     initializer = tf.random_normal_initializer(stddev=0.02, seed=None),
                                     trainable = self.train)
        
    def embed(self, X, we):
        e = tf.gather(we, X)
        h = tf.reduce_sum(e, 2)
        return h
    
    def dropout(self, x):
        if self.train and self.pdrop > 0:
            x = tf.nn.dropout(x, self.pdrop)
        return x
        
    def call(self, X):
        if self.freeze_emb:
            emb = tf.keras.layers.Concatenate(axis = 0)([self.w_embed_f1, self.w_embed_t,self.w_embed_f2])
            h = self.embed(X, emb)
            return [h,emb]
        else:
            emb = self.dropout(self.w_embed)
            h = self.embed(X, emb)
            return [h,self.w_embed]
    
    
    
class conv1d(tf.keras.layers.Layer):
    def __init__(
        self,
        nf,
        rf = 1,
        w_init = tf.random_normal_initializer(stddev=0.02),
        b_init = tf.constant_initializer(0),
        pad = 'VALID',
        train = False,
    ):
        super(conv1d, self).__init__()
        self.rf = rf
        self.nf = nf
        self.w_init = w_init
        self.b_init = b_init
        self.pad = pad
        self.train = train

    def build(self, x):
        self.nx = x[-1]
        self.w = self.add_weight("conv_w", 
                                 shape = [self.rf, self.nx, self.nf], 
                                 initializer = self.w_init,trainable = self.train)
        self.b = self.add_weight("conv_b", 
                                 shape = [self.nf], 
                                 initializer = self.b_init,trainable = self.train)
        
    def call(self, x):
        
        
        if self.rf == 1: 

            c = tf.einsum('ble,reo->blo', x, self.w)+self.b
            
        else: 
            c = tf.nn.conv1d(x, self.w, stride=1, padding = self.pad)+self.b
        return c
    
    
class conv1d_LoRA(tf.keras.layers.Layer):
    def __init__(
        self,
        nf,
        rf = 1,
        lora_dim = 4,
        scale = 32,
        w_init = tf.random_normal_initializer(stddev=0.02),
        b_init = tf.constant_initializer(0),
        pad = 'VALID',
        train = False,
    ):
        super(conv1d_LoRA, self).__init__()
        self.rf = rf
        self.nf = nf
        self.w_init = w_init
        self.b_init = b_init
        self.pad = pad
        self.train = train
        self.R = lora_dim
        self.scale = scale 
        

    def build(self, x):
        self.nx = x[-1]
        self.w = self.add_weight("conv_w", 
                                 shape = [self.rf, self.nx, self.nf], 
                                 initializer = self.w_init,trainable = False)
        self.b = self.add_weight("conv_b", 
                                 shape = [self.nf], 
                                 initializer = self.b_init,trainable = self.train)
        
        self.A = self.add_weight("LoRA_A", 
                                 shape = [self.rf, self.R, self.nf], 
                                 initializer = self.b_init,trainable = self.train)
        
        self.B = self.add_weight("LoRA_B", 
                                 shape = [self.rf, self.nx, self.R], 
                                 initializer = tf.constant_initializer(0),trainable = self.train)
        
    def call(self, x):
        
        
        if self.rf == 1: 
            
            c = tf.einsum('ble,reo->blo', x, self.w + 
                          (self.scale/self.R)*tf.einsum('...ij,...jk->...ik',self.B,self.A)) + self.b
              
        else: 
            
            c = tf.nn.conv1d(x, self.w + 
                             (self.scale/self.R)*tf.einsum('...ij,...jk->...ik',self.B,self.A), stride=1, padding = self.pad) + self.b
            
        return c
    
    
class MHA(tf.keras.layers.Layer):
    def __init__(
        self,
        num_heads,
        key_dim,
        pdrop = 0.0,
        rdrop = 0.0,
        random_features = None,
        scale = False,
        train = False,
        LoRA = False,
        lora_dim = None,
        FAVOR = False,
        seed = 1337,
        **kwargs,
    ):
        super(MHA,self).__init__(**kwargs)
        self.num_heads = num_heads
        self.key_dim = key_dim
        self.pdrop = pdrop
        self.rdrop = rdrop
        self.scale = scale
        self.train = train
        self.LoRA = LoRA
        self.lora_dim = lora_dim
        self.FAVOR = FAVOR
        self.random_features = random_features
        if seed == None:
            self.seed = 0
        else:
            self.seed = seed
        
    def build(self, x):
        assert self.key_dim % self.num_heads == 0, "K/Q dimension not divisible by number of heads"
        if self.LoRA:
            self.conv_inp = conv1d_LoRA(nf = self.key_dim*3, train = self.train,lora_dim = self.lora_dim)
            self.conv_out = conv1d_LoRA(nf = self.key_dim, train = self.train,lora_dim = self.lora_dim)
        else:
            self.conv_inp = conv1d(nf = self.key_dim*3, train = self.train)
            self.conv_out = conv1d(nf = self.key_dim, train = self.train)
        
        if self.FAVOR == False:
            self.mask = mask()
        else:
            self.random_matrix = tf.keras.initializers.Orthogonal(seed = self.seed)
            

    def split_states(self,x, n):
        x_shape = shape_list(x)
        m = x_shape[-1]
        new_x_shape = x_shape[:-1]+[n, m//n]
        return tf.reshape(x, new_x_shape)
    
    def dropout(self, x,drop):
        if self.train and drop > 0:
            x = tf.nn.dropout(x, drop)
        return x
    
    def split_heads(self,x, n, k=False):
        return tf.transpose(self.split_states(x, n), [0, 2, 1, 3])
        
    def _attn(self,q, k, v):
        w = tf.einsum('...ij,...kj->...ik', q, k)
        if self.scale:
            n_state = shape_list(v)[-1]
            w = w*tf.math.rsqrt(tf.cast(n_state, tf.float32))
        
        w = self.mask(w)
        w = tf.nn.softmax(w)
        
        w = self.dropout(w,drop = self.pdrop )
        
        a = tf.einsum('...ij,...jk->...ik', w, v)
        return a
    
    def merge_heads(self,x):
        x = tf.transpose(x, [0, 2, 1, 3])
        x_shape = shape_list(x)
        new_x_shape = x_shape[:-2]+[np.prod(x_shape[-2:])]
        return tf.reshape(x, new_x_shape)
    
    def RFM_softmax(self,sequence,omega,D,query = False):
        e = 1e-06
        D = tf.math.rsqrt(tf.dtypes.cast(D,tf.float32))
        sequence = sequence * tf.math.rsqrt(tf.math.sqrt(tf.dtypes.cast(tf.shape(sequence)[-1],tf.float32)))
        sequence = tf.einsum("bhld,fd->bhlf", sequence, omega) 
        diag_data = tf.math.square(sequence)
        diag_data = tf.math.reduce_sum(sequence, axis=tf.keras.backend.ndim(sequence) - 1) / 2.0
        diag_data = tf.expand_dims(diag_data, axis=tf.keras.backend.ndim(sequence) - 1)
        
        last_dims_t = (len(sequence.shape) - 1,)
        attention_dims_t = (len(sequence.shape) - 3,)
        
        if query:
          sequence = D * (tf.math.exp(sequence - diag_data - tf.math.reduce_max(sequence, axis=last_dims_t, keepdims=True)) + e)
        else:
          sequence = D * (tf.math.exp(sequence - diag_data - tf.math.reduce_max(sequence, axis=last_dims_t + attention_dims_t, keepdims=True)) + e)
        return sequence
    
        
    def call(self, x):
        x = self.conv_inp(x)
        q, k, v = tf.split(x, 3, 2)
        
        q = self.split_heads(q, self.num_heads)
        k = self.split_heads(k, self.num_heads)
        v = self.split_heads(v, self.num_heads)
        
        if self.FAVOR:
            
            rm = self.random_matrix(shape=(self.random_features, tf.shape(q)[-1]))
            q = self.RFM_softmax(sequence = q,omega = rm,D = self.random_features,query = True)
            k = self.RFM_softmax(sequence = k,omega = rm,D = self.random_features)
            q = tf.transpose(q, [2, 0, 1, 3])  
            k = tf.transpose(k, [2, 0, 1, 3])  
            v = tf.transpose(v, [2, 0, 1, 3])  
            a = Attention_matrix(q, k, v)
            D = Attention_scaling(q, k)
            D = tf.expand_dims(D, axis=-1)
            a = a / (D+1e-6)
            a = tf.transpose(a, [1, 2, 0, 3]) 
            
        else:
            a = self._attn(q, k, v)

        a = self.merge_heads(a)
        a = self.conv_out(a)
        a = self.dropout(a,drop = self.rdrop )
        return a
    
    
class MLP(tf.keras.layers.Layer):
    def __init__(
        self,
        n_state,
        train,
        mdrop = 0.,
        LoRA = False,
        afn = 'gelu',
        lora_dim = None
    ):
        super(MLP, self).__init__()
        self.afn = afn
        self.train = train
        self.n_state = n_state
        self.drop = mdrop
        self.LoRA = LoRA
        self.lora_dim = lora_dim 
        

    def build(self, x):
        self.nx = x[-1]
        self.act = act_fns[self.afn]
        if self.LoRA:
            self.c_fc = conv1d_LoRA(nf = self.n_state, train = self.train, rf = 1,lora_dim = self.lora_dim)
            self.c_proj = conv1d_LoRA(nf = self.nx, train = self.train, rf = 1,lora_dim = self.lora_dim)
        else:
            self.c_fc = conv1d(nf = self.n_state, train = self.train, rf = 1)
            self.c_proj = conv1d(nf = self.nx, train = self.train, rf = 1)

        
    def dropout(self, x, pdrop, train):
        if self.train and pdrop > 0:
            x = tf.nn.dropout(x, pdrop)
        return x
        
    def call(self, X):
        h = self.act(self.c_fc(X))
        h2 = self.c_proj(h)
        h2 = self.dropout(h2, self.drop, self.train)
        return h2
    
    
class block(tf.keras.layers.Layer):
  def __init__(self, 
               train,
               n_head,
               pdrop = .0,
               rdrop = .0,
               mdrop = .0,
               lora_dim = None,
               scale = True,
               LoRA = False,
               FAVOR = False,
               random_features = None
               ):
    super(block, self).__init__()
    self.train = train
    self.pdrop = pdrop
    self.rdrop = rdrop
    self.mdrop = mdrop
    self.scale = scale
    self.n_head = n_head
    self.LoRA = LoRA
    self.lora_dim = lora_dim
    self.FAVOR = FAVOR
    self.random_features = random_features

  def build(self, x):
      nx = x[-1]
      self._mha = MHA(pdrop = self.pdrop, rdrop = self.rdrop,
                      key_dim = nx, num_heads = self.n_head, 
                      train = self.train, scale = self.scale,
                      LoRA = self.LoRA,lora_dim = self.lora_dim,
                      FAVOR = self.FAVOR,random_features = self.random_features)
      
      self.norm1 = norm()
      
      self._mlp = MLP(n_state = nx*4, train = self.train,mdrop = self.mdrop,LoRA = self.LoRA,lora_dim = self.lora_dim)
      
      self.norm2 = norm()

  def call(self, x):
      a = self._mha(x)
      n = self.norm1(a+x)
      m = self._mlp(n)
      h = self.norm2(m+n)
      return h  

