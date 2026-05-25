from . import models


def post_init_hook(env):
    trial_rating = env.ref(
        "supplier_reliability_rating.supplier_reliability_rating_trial",
        raise_if_not_found=False,
    )
    if not trial_rating:
        return

    partners = env["res.partner"].with_context(active_test=False).search([])
    partners_without_rating = partners.filtered_domain([
        ("supplier_reliability_rating_id", "=", False),
    ])
    partners_without_rating.write({"supplier_reliability_rating_id": trial_rating.id})
    partners._compute_display_name()
