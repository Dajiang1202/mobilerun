import torch
import numpy as np

from .env.env import get_obs

def _load_model(position, model_path):
    from .models import model_dict
    model = model_dict[position]()
    model_state_dict = model.state_dict()
    pretrained = torch.load(model_path, map_location='cpu')
    pretrained = {k: v for k, v in pretrained.items() if k in model_state_dict}
    model_state_dict.update(pretrained)
    model.load_state_dict(model_state_dict)
    model.eval()
    return model

class DeepAgent:

    def __init__(self, position, model_path):
        self.model = _load_model(position, model_path)

    def act(self, infoset):
        # 只有一个合法动作时直接返回，这样会得不到胜率信息
        # if len(infoset.legal_actions) == 1:
        #     return infoset.legal_actions[0], 0

        obs = get_obs(infoset)
        z_batch = torch.from_numpy(obs['z_batch']).float()
        x_batch = torch.from_numpy(obs['x_batch']).float()
        y_pred = self.model.forward(z_batch, x_batch, return_value=True)['values']
        y_pred = y_pred.detach().cpu().numpy()

        best_action_index = np.argmax(y_pred, axis=0)[0]
        best_action = infoset.legal_actions[best_action_index]
        best_action_confidence = y_pred[best_action_index]
        # print(best_action, best_action_confidence, y_pred)
        return best_action, best_action_confidence
