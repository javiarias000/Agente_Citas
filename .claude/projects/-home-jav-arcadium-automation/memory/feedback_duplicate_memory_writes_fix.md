---

name: Duplicate Memory Writes Bug Fix
description: Fix for repeated message storage causing excessive DB writes
type: feedback

## Problem

The system was saving all messages to the `langchain_memory` table on every turn, including messages from previous turns. This caused:

- Exponential growth of DB writes
- Duplicate records in memory table
- Performance degradation
- Log spam with repeated "Mensaje guardado en memoria"

## Root Cause

In `save_state_node` (ArcadiumGraph) and `save_context_node` (DeyyGraph), the code saved ALL messages from `state["messages"]` without tracking which ones were already persisted. Since each turn loads full history from store and adds new messages, saving the entire messages list re-saved old messages repeatedly.

## Solution

Introduced `initial_message_count` field in the state to track how many messages were already stored before the current turn. Modified save nodes to only save messages from index `initial_message_count` onward.

### Changes Made

1. **arcadium_graph.py**
   - `load_conversation_context`: sets `state["initial_message_count"] = len(history)`
   - `save_state_node`: only saves `messages[initial_message_count:]`

2. **deyy_graph.py**
   - `DeyyState` now `total=False` to allow optional fields
   - `load_initial_context`: sets `initial_message_count`, handles `current_user_message`
   - `save_context_node`: only saves new messages

3. **state_machine_agent.py**
   - Sets `arcadium_state["initial_message_count"] = len(history)` before invoking graph

4. **deyy_agent.py**
   - Passes `current_user_message` instead of manually appending to messages (to avoid overwrite by load node)

## How It Works

Turn N:

- Load history from store: `len(history) = X`
- Set `initial_message_count = X`
- Add new user message + AI responses
- `messages` length becomes `X + K`
- Save only `messages[X:X+K]` (the new ones)

Next turn N+1:

- Load history from store now includes all messages from turn N (length = X+K)
- Set `initial_message_count = X+K`
- Continue...

No more duplicates.

## Testing

Run tests: `./run.sh test`

Check DB: `langchain_memory` table should now only contain unique messages per session in chronological order.
