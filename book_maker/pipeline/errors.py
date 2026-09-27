"""The one failure type every stage of the bundle pipeline raises."""


class PipelineError(RuntimeError):
    """A stage could not finish, with a message meant for the operator.

    Carries the stage it happened in so the harness can print the single
    `{stage} failed: {detail}` line without every raise site repeating the
    template. Nothing on this path is retried: a bundle stage either
    produced its artifact or it did not, and the recovery is a decision
    (edit the source, install pandoc, check the provider job), never
    another automatic attempt.
    """

    def __init__(self, detail, stage=None):
        super().__init__(str(detail))
        self.stage = stage
        self.detail = str(detail)
