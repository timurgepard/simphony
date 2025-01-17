import logging
logging.getLogger().setLevel(logging.CRITICAL)
import torch
import torch.nn as nn
import numpy as np
import gymnasium as gym
import pickle
import time
from symphony import Symphony, ReplayBuffer
import math


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(device)

#global parameters
# environment type. Different Environments have some details that you need to bear in mind.
option = 3
burst = False # big amplitude random moves in the beginning
tr_noise = True  #if extra noise is needed during training

explore_time = 5000
tr_between_ep_init = 15 # training between episodes
tr_between_ep_const = False
tr_per_step = 3 # training per frame/step
start_test = 250
limit_step = 2000 #max steps per episode
limit_eval = 2000 #max steps per evaluation
num_episodes = 10000000
start_episode = 0 #number for the identification of the current episode
total_rewards, total_steps, test_rewards, Q_learning = [], [], [], False


hidden_dim = 256
max_action = 1.0
fade_factor = 7 # fading memory factor, 7 -remembers ~30% of the last transtions before gradual forgetting, 1 - linear forgetting, 10 - ~50% of transitions, 100 - ~70% of transitions.
stall_penalty = 0.07 # moving is life, stalling is dangerous, optimal value = 0.07, higher values can create extra vibrations.
capacity = "full" # short = 100k, medium=300k, full=500k replay buffer memory size.





if option == -1:
    env = gym.make('Pendulum-v1')
    env_test = gym.make('Pendulum-v1', render_mode="human")

elif option == 0:
    #burst = True
    env = gym.make('MountainCarContinuous-v0')
    env_test = gym.make('MountainCarContinuous-v0', render_mode="human")

elif option == 1:
    env = gym.make('HalfCheetah-v4')
    env_test = gym.make('HalfCheetah-v4', render_mode="human")

elif option == 2:
    tr_between_ep_init = 70
    env = gym.make('Walker2d-v4')
    env_test = gym.make('Walker2d-v4', render_mode="human")

elif option == 3:
    tr_between_ep_init = 200
    env = gym.make('Humanoid-v4')
    env_test = gym.make('Humanoid-v4', render_mode="human")

elif option == 4:
    limit_step = 300
    limit_eval = 300
    tr_between_ep_init = 70
    env = gym.make('HumanoidStandup-v4')
    env_test = gym.make('HumanoidStandup-v4', render_mode="human")

elif option == 5:
    env = gym.make('Ant-v4')
    env_test = gym.make('Ant-v4', render_mode="human")
    #Ant environment has problem when Ant is flipped upside down and it is not detected (rotation around x is not checked, only z coordinate), we can check to save some time:
    angle_limit = 0.4
    #less aggressive movements -> faster learning but less final speed
    max_action = 0.7

elif option == 6:
    tr_between_ep_init = 40
    burst = True
    tr_noise = False
    limit_step = int(1e+6)
    env = gym.make('BipedalWalker-v3')
    env_test = gym.make('BipedalWalker-v3', render_mode="human")

elif option == 7:
    burst = True
    tr_noise = False
    tr_between_ep_init = 0
    env = gym.make('BipedalWalkerHardcore-v3', render_mode="human")
    env_test = gym.make('BipedalWalkerHardcore-v3')

elif option == 8:
    limit_step = 700
    limit_eval = 700
    env = gym.make('LunarLanderContinuous-v2')
    env_test = gym.make('LunarLanderContinuous-v2')

elif option == 9:
    limit_step = 300
    limit_eval = 200
    env = gym.make('Pusher-v4')
    env_test = gym.make('Pusher-v4', render_mode="human")

elif option == 10:
    burst = True
    env = gym.make('Swimmer-v4')
    env_test = gym.make('Swimmer-v4', render_mode="human")



state_dim = env.observation_space.shape[0]
action_dim= env.action_space.shape[0]

print('action space high', env.action_space.high)
max_action = max_action*torch.FloatTensor(env.action_space.high).to(device) if env.action_space.is_bounded() else max_action*1.0
replay_buffer = ReplayBuffer(state_dim, action_dim, capacity, device, fade_factor, stall_penalty)
algo = Symphony(state_dim, action_dim, hidden_dim, device, max_action, burst, tr_noise)



#used to create random initalization in Actor -> less dependendance on the specific random seed.
def init_weights(m):
    if isinstance(m, nn.Linear): torch.nn.init.xavier_uniform_(m.weight)



#testing model
def testing(env, limit_step, test_episodes):
    if test_episodes<1: return
    print("Validation... ", test_episodes, " epsodes")
    episode_return = []

    for test_episode in range(test_episodes):
        state = env.reset()[0]
        rewards = []

        for steps in range(1,limit_step+1):
            action = algo.select_action(state, replay_buffer, mean=True)
            #action = algo.select_action(state, mean=True)
            next_state, reward, done, info , _ = env.step(action)
            rewards.append(reward)
            state = next_state
            
            if done: break

        episode_return.append(np.sum(rewards))

        validate_return = np.mean(episode_return[-100:])
        print(f"trial {test_episode}:, Rtrn = {episode_return[test_episode]:.2f}, Average 100 = {validate_return:.2f}, steps: {steps}")





#--------------------loading existing models, replay_buffer, parameters-------------------------

try:
    print("loading buffer...")
    with open('replay_buffer', 'rb') as file:
        dict = pickle.load(file)
        algo.actor.noise.x_coor = dict['x_coor']
        replay_buffer = dict['buffer']
        total_rewards = dict['total_rewards']
        total_steps = dict['total_steps']
        average_steps = dict['average_steps']
        if len(replay_buffer)>=explore_time and not Q_learning: Q_learning = True
    print('buffer loaded, buffer length', len(replay_buffer))

    start_episode = len(total_steps)

except:
    print("problem during loading buffer")

try:
    print("loading models...")
    algo.actor.load_state_dict(torch.load('actor_model.pt'))
    algo.critic.load_state_dict(torch.load('critic_model.pt'))
    algo.critic_target.load_state_dict(torch.load('critic_target_model.pt'))
    algo.replay_buffer = replay_buffer
    print('models loaded')
    testing(env_test, limit_eval, 10)
except:
    print("problem during loading models")

#-------------------------------------------------------------------------------------

for i in range(start_episode, num_episodes):
    
    rewards = []
    state = env.reset()[0]

    #----------------------------pre-processing------------------------------
    #---------------------0. increase ep training: -------------------------
    rb_len = len(replay_buffer)
    rb_len_treshold = 5000*tr_between_ep_init
    tr_between_ep = tr_between_ep_init
    if not tr_between_ep_const and tr_between_ep_init>=100 and rb_len>=350000: tr_between_ep = rb_len//5000 # init -> 70 -> 100
    if not tr_between_ep_const and tr_between_ep_init<100 and rb_len>=rb_len_treshold: tr_between_ep = rb_len//5000# init -> 100
    #---------------------------1. processor releave --------------------------
    if Q_learning: time.sleep(0.5)
    #---------------------2. decreases dependence on random seed: ---------------
    if not Q_learning and rb_len<explore_time:
        algo.actor.apply(init_weights)
    #-----------3. slighlty random initial configuration as in OpenAI Pendulum----
    action = 0.3*max_action.to('cpu').numpy()*np.random.uniform(-1.0, 1.0, size=action_dim)
    for steps in range(0, 2):
        next_state, reward, done, info, _ = env.step(action)
        rewards.append(reward)
        state = next_state
    
    

    #------------------------------training------------------------------

    if Q_learning: _ = [algo.train(replay_buffer.sample()) for x in range(tr_between_ep)]
        
    episode_steps = 0
    for steps in range(1, limit_step+1):
        episode_steps += 1

        if len(replay_buffer)>=explore_time and not Q_learning:
            print("started training")
            Q_learning = True
            _ = [algo.train(replay_buffer.sample()) for x in range(128)]

        action = algo.select_action(state, replay_buffer)
        #action = algo.select_action(state)
        next_state, reward, done, info, _ = env.step(action)
        rewards.append(reward)
        
        #==============counters issues with environments================
        #(scores in report are not affected)
        #Ant environment has problem when Ant is flipped upside down and it is not detected (rotation around x is not checked), we can check to save some time:
        if env.spec.id.find("Ant") != -1:
            if (next_state[1]<angle_limit): done = True
            if next_state[1]>1e-3: reward += math.log(next_state[1]) #punish for getting unstable.
        #Humanoid-v4 environment does not care about torso being in upright position, this is to make torso little bit upright (z↑)
        #elif env.spec.id.find("Humanoid-") != -1:
            #reward += next_state[0]
        #fear less of falling/terminating. This Environments has a problem when agent stalls due to the high risks prediction. We decrease risks to speed up training.
        elif env.spec.id.find("LunarLander") != -1:
            if reward==-100.0: reward = -50.0
        #fear less of falling/terminating. This Environments has a problem when agent stalls due to the high risks prediction. We decrease risks to speed up training.
        elif env.spec.id.find("BipedalWalkerHardcore") != -1:
            if reward==-100.0: reward = -25.0
        #===============================================================
        
        replay_buffer.add(state, action, reward+1.0, next_state, done)
        if Q_learning: _ = [algo.train(replay_buffer.sample()) for x in range(tr_per_step)]
        state = next_state
        if done: break


    total_rewards.append(np.sum(rewards))
    average_reward = np.mean(total_rewards[-100:])

    total_steps.append(episode_steps)
    average_steps = np.mean(total_steps[-100:])


    print(f"Ep {i}: Rtrn = {total_rewards[-1]:.2f} | ep steps = {episode_steps}")


    if Q_learning:

        #--------------------saving-------------------------
        if (i%5==0): 
            torch.save(algo.actor.state_dict(), 'actor_model.pt')
            torch.save(algo.critic.state_dict(), 'critic_model.pt')
            torch.save(algo.critic_target.state_dict(), 'critic_target_model.pt')
            #print("saving... len = ", len(replay_buffer))
            with open('replay_buffer', 'wb') as file:
                pickle.dump({'buffer': replay_buffer, 'x_coor':algo.actor.noise.x_coor, 'total_rewards':total_rewards, 'total_steps':total_steps, 'average_steps': average_steps}, file)
            #print(" > done")


        #-----------------validation-------------------------
        if (i>=start_test and i%50==0): testing(env_test, limit_step=limit_eval, test_episodes=10)
              

#====================================================
# * Apart from the algo core, fade_factor, tr_between_ep and limit_steps are crucial parameters for speed of training.
#   E.g. limit_steps = 700 instead of 2000 introduce less variance and makes BipedalWalkerHardcore's Agent less discouraged to go forward.
#   high values in tr_between_ep can make a "stiff" agent, but sometimes it is helpful for straight posture from the beginning (Humanoid-v4).
