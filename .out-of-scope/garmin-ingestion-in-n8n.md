# Garmin Ingestion in n8n

MyCoach keeps Garmin authentication, fetching and mapping in Python. It does not move Garmin sync into an n8n workflow.

## Why this is out of scope

Garmin Connect has no official API and n8n has no Garmin node. Today the `garminconnect` library handles login, the token cache and the email/password fallback (`src/mycoach/sources/garmin/client.py`). A workflow would have to reimplement that as raw HTTP calls against an unofficial API, plus session refresh, so every Garmin-side change would break a workflow instead of arriving as a library upgrade.

The payoff the request wanted, "adding a source means building a workflow", only shows up with a second external source. None exists: Hevy was replaced by the Logger, which is internal, and Strava has no code or stated need.

Moving only the scheduling and POSTing, with a Python fetch service behind it, keeps the Python adapter and adds a network hop, so it gives up most of the benefit.

## What is not rejected

A callable HTTP intake for raw payloads (the constraint the #85 map committed to, see `src/mycoach/sources/intake.py`) is deferred, not rejected. It gets built when a real second external source appears. That would be a new issue, not a reopening of this one.

## Prior requests

- #106: "Move health-data ingestion to n8n workflows"
