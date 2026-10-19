# GitHub Actions deployment

The workflow in `.github/workflows/ci-cd.yml` checks the backend and frontend,
then builds and deploys the backend on pushes to `main`. It also checks pull
requests without deploying. The deploy job preserves the existing Cloud Run
configuration and uses the repository commit SHA as the image tag.

GitHub authenticates through Google Cloud Workload Identity Federation with a
dedicated service account, without a long-lived service-account key. The
provider is restricted to this repository's immutable GitHub owner and
repository IDs, push or manual events, and the `main` branch. The service
account can push images to `cloud-run-source-deploy`, update
`paralegal-voice-agent`, read Cloud Run operations, and use its existing runtime
service account.

Once the Google Cloud identity and IAM bindings are configured, commit and push
the workflow to `main` to activate it. Check the repository's Actions tab for
the first run. The workflow can also be run manually from that tab on `main`.
Each run builds from its commit; an older run that reaches deployment after a
newer commit was pushed is skipped. A failed check prevents deployment.

If the deploy step fails, inspect its Actions logs and verify the workload
identity provider, service-account IAM bindings, Artifact Registry repository,
and Cloud Run service. Cloud Run retains earlier revisions; use its console to
direct traffic to a known-good revision if a deployed change causes a
regression, and revert the change on `main`. This workflow does not perform
application-level automatic rollbacks.
