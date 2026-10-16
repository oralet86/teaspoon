# Notes for the reward in Claim 2

- Keep running estimates of improvement and of time per operator and divide
  them. Averaging single ratios is noisier, because most uses improve nothing.
- If a baseline is wanted, use the current average rate. A reward that is
  positive only when an operator beats 2-opt builds the answer in.
- Pursuit-style selectors only use the ranking, so a baseline changes nothing
  for them; probability matching breaks on negative rewards.
