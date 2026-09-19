Savanna: Terminology of Products and Programmes | v1.11 | Controlled Draft | Internal / Partner-Shared

# Savanna: Terminology of Products and Programmes

*Part of the Wanda Savanna Document Suite*

| **Document Status** | **Controlled Draft** |
| --- | --- |
| **Version** | 1.11 |
| **Date** | 29-07-2026 |
| **Owner** | CEngO |
| **Classification** | Internal / Partner-Shared |

> This is a partner-shared copy of Wanda's canonical terminology reference, prepared for the altcloud engagement. It is regenerated from the internal original on each code drop — treat it as read-only reference material.

This document defines Wanda’s ecosystem terminology and architectural components. It establishes the strategic structure of the Savanna ecosystem and the canonical language used to describe it.

It does not assign delivery scope, commercial responsibility, or implementation obligations.

All delivery documentation, architectural artefacts, and technical specifications must use Wanda’s canonical terminology.

# 1. Naming Conventions and Suffix Rules

.ML denotes Machine Learning–led capabilities.

.AI denotes Artificial Intelligence capabilities, including Large Language Models.

These naming conventions apply consistently across the Savanna ecosystem and form part of Wanda’s canonical terminology.

# 2. Savanna: The Ecosystem

Savanna is Wanda’s complete technology ecosystem.

It represents the integrated suite of applications, intelligence platforms, data foundations, commercial data services, cloud infrastructure, and governance architecture that power Wanda’s services for PBMs, union clients, and members.

Savanna defines both Wanda’s commercial ecosystem and its architectural boundary.

Savanna includes:

- Application layer: Summit, Vista

- Intelligence layer: Expedition, consisting of Horizon.ML and Ascent.AI

- Data foundation: Elevate

- Intelligence and Data-as-a-Service suite: Perspective

- Cloud and platform foundation: Strata.Cloud, including Strata.Engine (and its Strata.Engine.Auth module), Strata.Terminology, Strata.Core, and Strata.Connect

- Security, governance, and regulatory architecture: Halo

- Transformation programme: Oasis

Savanna is the umbrella ecosystem within which all other components operate.

# 3. Applications

## 3.1 Summit: Professional Web Portal

Summit is Wanda’s professional web portal.

It is used by:

- Wanda

- Linc, coaching partner

- Sav-Rx and other PBMs

Summit supports:

- Coaching workflows

- Member management

- Operational oversight

Summit (legacy) does not support Expedition capabilities.

Summit, delivered through Oasis, separates the web front end from backend services and is powered by Strata.Engine. Expedition capabilities, including Horizon.ML and Ascent.AI, are introduced subsequently through the phased Expedition programme from 2027, after the MVP launch.

# 4. Expedition: Intelligence Layer

Expedition represents Wanda’s AI and Machine Learning vision.

It is delivered through two platforms:

- Horizon.ML

- Ascent.AI

Expedition is a phased programme of work introduced from 2027, after the Savanna MVP launch, building on the platform foundations established by the MVP.

Together, Horizon.ML and Ascent.AI form the intelligence layer of the Savanna ecosystem.

## 4.1 Horizon.ML: Predictive and Data Intelligence

Horizon.ML provides predictive and data intelligence capabilities across Savanna.

Roles include:

- Steward: Data stewardship, ensuring data quality, completeness, and appropriate capture

- Scout: Predictive modelling and risk detection

Horizon.ML enables:

- Data quality assurance and governance

- Population-scale predictive capabilities

- Behavioural and programme insight

Horizon.ML operates across the Savanna ecosystem and is powered by Elevate.

## 4.2 Ascent.AI: Virtual Coaching and Orchestration Intelligence

Ascent is Wanda’s overall coaching programme.

Ascent.Coaches are the human professionals delivering the programme today.

Ascent.AI represents the virtual coaching and orchestration layer introduced as part of Oasis.

It introduces:

- Guide: Conversational AI coaching

- Navigator: Workflow orchestration across Summit and Vista

Ascent.AI supports professional coaches through Summit and supports end users directly through Vista.

Ascent.AI is not live within Summit (legacy) or Vista (legacy).

# 5. Elevate: Intelligence Foundation and Data Warehouse

Elevate is Wanda’s central longitudinal data and intelligence platform.

It:

- Receives and structures business event streams emitted across Savanna

- Serves as the unified foundation for AI and Machine Learning, analytics, and reporting

- Powers Expedition intelligence and Perspective services

Elevate is a retained strategic asset within Savanna and represents a key competitive moat.

While Strata.Engine introduces architectural evolution, Elevate is being rebuilt from bronze upward with targeted updates to data models, event structures, and integration patterns to align with the Savanna platform architecture.

# 6. Perspective: Intelligence and Data-as-a-Service Suite

Perspective is a structured commercial intelligence and data suite within Savanna, built on Elevate.

It enables scalable, enterprise-grade insight and controlled data access.

Perspective consists of two primary service categories:

## 6.1 Intelligence-as-a-Service

Provides curated insight products derived from Elevate and Expedition outputs.

This includes:

- Standard reports

- Enhanced reports

- Bespoke reports

- Operational reporting, management reporting, KPIs, and billing reporting

Reporting capabilities are currently delivered through tools such as Power BI connected to Elevate.

## 6.2 Data-as-a-Service

Provides structured enterprise data access models within Savanna.

This includes:

- Single-tenant Wanda-hosted Elevate instances enabling controlled customer access to programme data

- Bespoke data pipelines synchronizing Wanda data into customer-owned data warehouses

- Data pipelines to Expedition services

Data-as-a-Service enables enterprise integration while maintaining governance controls defined by Halo.

Perspective strengthens Wanda’s data moat and supports long-term enterprise positioning.

# 7. Strata: Cloud and Platform Foundation

Strata.Cloud is Wanda’s AWS-based infrastructure and platform foundation layer.

Strata.Cloud is the umbrella platform foundation; Strata.Engine (with its Strata.Engine.Auth module), Strata.Terminology, Strata.Core, and Strata.Connect are logical service domains operating within it.

Strata.Cloud provides:

- AWS infrastructure

- Identity and access management aligned with Halo

- Core operational services

- Domain services and API infrastructure

- Integration services

Strata.Cloud underpins all Savanna components.

## 7.1 Strata.Engine

Strata.Engine is the API-first business logic and workflow execution layer of Savanna.

It powers Summit and Vista, orchestrates domain workflows, and enforces application-level business rules.

## 7.2 Strata.Core

Strata.Core represents the operational data domain and governed system-of-record within Savanna.

It manages live transactional data stores that maintain real-time member, coaching, and programme state.

Strata.Core includes the data access layer and persistence patterns through which application services interact with operational data. Its schema, migrations, and fixture data ship as the canonical `strata-core` package, consumed by every Strata.Engine service.

Strata.Core is distinct from Elevate.

Elevate serves as the longitudinal intelligence and analytics foundation; Strata.Core supports day-to-day platform operations, while Strata.Engine Workers emit structured business events into Elevate to enable Expedition and Perspective capabilities.

## 7.3 Strata.Connect

Strata.Connect operates as the third-party integration layer.

It performs cloud-to-cloud integrations, including SmartMeter connectivity for cellular-connected scales and blood pressure monitors.

It will evolve toward standards-based integrations, including HL7 FHIR.

## 7.4 Strata.Terminology

Strata.Terminology is the clinical terminology (reference-data) service within Strata.Cloud.

It owns Savanna's clinical reference data — FDA NDC medications, ICD-10-PCS procedures, and ICD-10-CM diagnoses — ingesting the authoritative external sources and serving authenticated search APIs consumed by Summit and, later, Vista. The data belongs to no single product. In the strata-cloud repository it lives in its own top-level folder, `strata.terminology/`.

## 7.5 Strata.Engine.Auth

Strata.Engine.Auth is the dedicated authentication and authorisation module within Strata.Cloud.

It integrates AWS Cognito, validates access tokens on every request, and enforces both coarse-grained role-claim checks and fine-grained authorisation against tenant, programme, coach, and member context.

It is the implementation of Halo's identity and access-control models within the platform, consumed by Strata.Engine services and Strata.Connect. In the strata-cloud repository it lives in its own top-level folder, `strata.engine.auth/`.

# 8. Halo: Security, Governance and Regulatory Architecture

Halo is Wanda’s cross-cutting security, governance, and regulatory architecture across the Savanna ecosystem.

It defines how identity, access control, policy enforcement, audit, and regulatory alignment are applied consistently across applications, intelligence platforms, data systems, and infrastructure.

Halo applies to:

- Human users

- Intelligent agents operating within Horizon.ML and Ascent.AI

- Application and API services

- Data platforms and infrastructure components

A core principle of Halo is consistent governance across both human and AI actors. Intelligent agents are treated as governed actors within the platform, subject to defined identity, permissioning, policy enforcement, and audit controls.

Halo encompasses:

- Identity and authorisation models, including role-based and policy-based access control

- AI model oversight and guardrails

- Logging, traceability, and audit mechanisms

- Organisational security policies and procedures

- Information Security Management System alignment

- Regulatory and standards alignment, including HIPAA, ISO 27001, and ISO 13485

- Technical assurance mechanisms such as code scanning and infrastructure monitoring

Halo is formalised as part of Oasis and applies uniformly across Savanna.

# 9. Oasis: The Transformation Programme

Oasis is not a product.

It is the structured, governance-led transformation programme that transitions Wanda from the legacy platform to Savanna.

Oasis delivers:

- Architectural modularisation

- Summit, a full rewrite of Summit (legacy)

- Separation of Summit into a web front end powered by Strata.Engine

- Formalisation of Strata.Cloud

- Strata.Engine, the API-first backend services layer powering Summit and Vista

- Formalisation and evolution of Strata.Connect

- Vista, evolution of Vista (legacy) where feasible

- Introduction of Expedition capabilities across Summit and Vista

- Halo formalisation

- Controlled migration and cutover

Savanna is the target architecture delivered through Oasis.

Part of the Wanda Savanna Document Suite | Classification: Internal / Partner-Shared | Owner: CEngO