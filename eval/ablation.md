# Ablation (fast mode: cheap model only, same prompts and ranking)

Dev = the 10 episodes that exposed v1's failures (v2 was designed after seeing them, so gains there are optimistic). Fresh = 4 episodes labelled blind before v2 was built.

| Variant | Pools | Candidates | Set | Recall | Ep top-3 | Acq top-20 | Eng top-20 | Boundary err | Weak eps OK | Acq top-20 pools |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | A,B:duration | 288 | dev | 6/8 | 5/8 | 2/8 | 1/8 | 4.9s | 2/2 | {'A': 17, 'B': 3} |
| v1 | A,B:duration | 288 | fresh | 1/3 | 1/3 | 0/3 | 0/3 | 0.4s | 1/1 |  |
| v1 + Pool C | A,B:duration,C | 341 | dev | 8/8 | 7/8 | 2/8 | 2/8 | 4.2s | 2/2 | {'A': 13, 'C': 3, 'B': 4} |
| v1 + Pool C | A,B:duration,C | 341 | fresh | 1/3 | 1/3 | 0/3 | 0/3 | 0.4s | 1/1 |  |
| v1 + hook search | A,B:hooks | 351 | dev | 5/8 | 5/8 | 1/8 | 1/8 | 2.1s | 2/2 | {'A': 13, 'B': 7} |
| v1 + hook search | A,B:hooks | 351 | fresh | 1/3 | 1/3 | 1/3 | 0/3 | 0.0s | 1/1 |  |
| v2 | A,B:hooks,C | 404 | dev | 7/8 | 7/8 | 3/8 | 2/8 | 2.1s | 2/2 | {'A': 13, 'C': 3, 'B': 4} |
| v2 | A,B:hooks,C | 404 | fresh | 1/3 | 1/3 | 0/3 | 0/3 | 0.0s | 1/1 |  |
