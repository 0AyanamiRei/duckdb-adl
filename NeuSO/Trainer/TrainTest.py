import torch
from Plan.plan_enumerator import PlanEnumerator

class TrainTest:
    def __init__(self, model_list, device):
        self.query_model, self.state_cost_model, self.state_card_model, self.wcoj_cost_model = model_list
        self.models = [self.query_model, self.state_cost_model, self.state_card_model, self.wcoj_cost_model]
        self.plan_enumerator = PlanEnumerator(self.state_cost_model, self.state_card_model, self.wcoj_cost_model)
        self.device = device
        self.model_to_device()

    def model_to_device(self):
        for model in self.models:
            model.to(self.device)

    def model_train(self):
        for model in self.models:
            model.train()

    def model_eval(self):
        for model in self.models:
            model.eval()

    def save_models(self, save_path):
        state = {
            'query_model_state_dict': self.query_model.state_dict(),
            'cost_model_state_dict': self.state_cost_model.state_dict(),
            'card_model_state_dict': self.state_card_model.state_dict(),
            'wcoj_model_state_dict': self.wcoj_cost_model.state_dict()
        }
        torch.save(state, save_path)

    def resume_models(self, resume_path):
        print(f"Loading from checkpoint: {resume_path}")
        resume_path = str(resume_path)
        checkpoint = torch.load(resume_path)

        self.query_model.load_state_dict(checkpoint['query_model_state_dict'])
        self.state_cost_model.load_state_dict(checkpoint['cost_model_state_dict'])
        self.state_card_model.load_state_dict(checkpoint['card_model_state_dict'])
        self.wcoj_cost_model.load_state_dict(checkpoint['wcoj_model_state_dict'])
