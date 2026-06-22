# 原始想法

我想探索一种面向具身智能的 world action model。直觉是，很多 robot manipulation 或 rope/cloth interaction 任务中，世界模型只预测状态转移，但没有显式表达动作在物理接触、可控性和长时序规划中的角色。

一个可能方向是把 action-conditioned dynamics、contact-rich manipulation、representation learning 和 differentiable simulation 结合起来，让模型不只是预测下一帧，而是学到“哪些动作会改变世界中哪些可控因素”。我担心这个想法可能已经被 world model、model-based reinforcement learning、affordance learning 或 controllable representation 相关工作覆盖。

我希望重点查：

- 是否已有论文提出 world action model 或类似概念。
- action-conditioned world model 和 affordance / controllability representation 的关系。
- 这个方向在 rope grasping、deformable object manipulation 或 contact-rich robot learning 中是否有独立贡献空间。
- 如果做最小实验，应该如何设计 baseline、ablation 和失败判据。
