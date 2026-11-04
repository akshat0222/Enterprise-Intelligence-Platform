# Atlas Platform Product Specification

## Overview
Atlas is the customer-facing API platform. This document lists its published limits and commitments.

## API limits
The API rate limit is 1000 requests per minute per API key. Requests above the limit receive HTTP 429.
The maximum upload size is 20 MB per file.

## Data retention
Customer data is retained for 90 days after account closure and then permanently deleted.

## Authentication
Atlas uses OAuth 2.0. Access tokens expire after 1 hour and refresh tokens expire after 30 days.

## Availability
The platform uptime target is 99.9 percent, measured monthly.

## Regions
Atlas is deployed in two regions: eu-west and us-east.
