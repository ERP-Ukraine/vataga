from . import models


def post_init_hook(env):
    trial_rating = env.ref(
        "supplier_reliability_rating.supplier_reliability_rating_trial",
        raise_if_not_found=False,
    )
    if not trial_rating:
        return

    env.cr.execute(
        """
        UPDATE res_partner
           SET supplier_reliability_rating_id = %s
         WHERE supplier_reliability_rating_id IS NULL
        """,
        [trial_rating.id],
    )
