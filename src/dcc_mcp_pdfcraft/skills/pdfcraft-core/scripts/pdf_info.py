from dcc_mcp_core.skill import skill_entry

from dcc_mcp_pdfcraft.capability_dispatch import dispatch


@skill_entry
def main(**params):
    return dispatch("pdf_info", params)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
