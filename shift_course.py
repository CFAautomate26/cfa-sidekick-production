"""The Operator's leadership development course, mapped from Google Drive.

Source of truth is the "Leadership Development" folder on the store's Drive
(marketing.cfalondon@gmail.com). This module pins the course structure the
shift app seeds into course_lessons on first boot after upgrade; lessons are
(title, doc_file_id, slides_file_id, video_file_id) with None for materials
a lesson doesn't have. File ids come from Drive and remain valid as long as
the files exist, wherever they're moved.

Regenerating after course changes: re-list the Drive folder, update this
file, and either edit lessons in the deployed database to match or clear
the course_seeded meta row so the next boot re-seeds.
"""

COURSE_FOLDER_URL = "https://drive.google.com/drive/folders/1WRRCm0tqOGT8rwFp5T0KJLyUb-Kysop9"


def drive_url(file_id: str | None) -> str | None:
    return f"https://drive.google.com/file/d/{file_id}/view" if file_id else None


# (module name, [(title, doc_id, slides_id, video_id), ...]) in teaching order.
COURSE = [
    ("Mindset 101", [
        ("Intro", None, None, "1rvH4njwLzFN8TTrsfyk_ssGeywXcjaXV"),
        ("Compressing Time Frames", "14PVIbLMKkzdkdUEA5En9x0bLDWZ0TJf3",
         "1Ac4-CXZLx_g-hEpEe60-GUNk1uAakHos", "1iwl3IXBsRJLtSCnAOhZnLdxr5a5kQSqV"),
        ("Mini Days", "1thzZROH1mRAHGW8JfYk61x_yvp8Fi3bR",
         "1rv9j8BE8Y8jqPG_3Ydcz9efEL4OUhMKM", "1dSy6hvWJwck4O_o3nj_d4oGE8HRIFA9z"),
        ("Winning Leadership Pyramid", "1g3Ft4KdKgaKLMM9JvE7C0QcPJcktUSQe",
         "1q5-I8goKJJns8GpQY3sQy_8tvZH5EBNm", "1eLBLpOuzQWTG0AFaFeVIp5qMcGEEZBN7"),
        ("Personal Growth", "1z0f4-1_Wf18WZmIMcykH1tWcKwKzSe2w",
         "1lsB2kxUIH2k7rkOWwBoxyTOwW-tzl6Yj", "1iw7oIOOIX1zB3Q-jErmKgBvXWu_IugTa"),
        ("The Self Aware Leader", "1dYbJjeXT5fjpSpi4qchoo2uZamNd0wcU",
         "1ui0HBIv_fib0geDg-0AY1Yo0v47jqo-X", "1zL06XfAowsVupqTmRsbjpQ_etxpJ6wb1"),
        ("Humility", "1LH-Xvl1ZvdRiQiM9wJNhOJ7J8SvmhBI9",
         "1YrBOBijE1O8W_CjwPpMtCI4gq12XrvxR", "1y6RfmssngFiNC0WZ0ZxxVjlMiYDcAIAq"),
        ("Ego", "1WCj7sbseAebBY1XnTceiGIIvAUfkKXzt",
         "1r7WkeHwY47VOWXo9CU1-grZIQXS0a1Oe", "19bFopYwyiGsgM96ieBGj7b9sowiBUvRH"),
        ("Grit", "1NmAkwmoTsjC-GJt0yw5Su6VNLYTrlC3o",
         "1QSUQg9SEpr_lE2UWPhbUnel2U1R2QoWL", "1ZPGFhIbBuM_cIvEtfvcaOJW8gnHOP4aj"),
        ("Integrity", "1GqnoQByoSHdqDH-wogvdiUTHlR4E1Bdl",
         "1j0zxuP-xxSbVv3zFfWg3zhgABV14OTHM", "1hTpXrndfHT6l3HMPnFdRMn86301GjGrT"),
        ("Ownership", "1Tb0bPl4nK56wyTDts_B-EncuLwEnH9g5",
         "1A9IMKEhAnDD37PAu3j2a534XFBNYq5ax", "16Cv5Vh2KDydWOQNizdw-xruEvcq0JNQb"),
        ("Curiosity", "1j62rbpHYN_odDjEHZdYBz4ibj599Lcr5",
         "1lg3MZiPdT1FvaZTE7JnlfhxLI5Mzd-fC", "1Seuj-0lYxq9meYGDcfYW6wdUmabvC-dl"),
        ("Fear", "1qbH3tJA1QvPCFGsfPwSQSVMHDji-VJR9",
         "1ceYZNorp9Y1TQclE2bjanvZ-C75uXA_I", "1oZLhZAGUtZUWDBSkKM3JGsMUgLfmgOvh"),
        ("Good vs. Bad Habits", "1MsSgZx99sV0PW17dKdoHrfRUpL_VYN9j",
         "1u4CwdvxZaabAuKMsqAPAfCVV2HmT4eYP", "1hn0H4NiBt2nuGE4C5UxOjIoubA72pbmJ"),
        ("Growth vs. Fixed Mindset", "12ArsYOcWBlYuNZ6AHsqHr1kOpxqk8nCV",
         "117YOXCJ6IlfQBbvka5w51mnuwUFoDTGJ", "1IMDzdZYOIEd2vFo7LgEd1m9OymVr0Lq0"),
    ]),
    ("Leading Others", [
        ("Waste 101", "193LCyQihWlS44E01xga45ULu8wnDDULv",
         "1eE91oXzj8OGgQrLaJn3JAwkgq44XlkUJ", None),
        ("SERVE Model", "1IVtu4S9dnkQ99ghHz-szbHKsvsgbZf2_",
         "105ba7vQwqSK8HMd0I3yYLxhiNZeoL1_5", None),
        ("Good vs. Bad Friction", "1UUrd_15ge66vlwkhpsGVx7RWmRL5ieXe",
         "1Vv1sbp3DdIzjtSgMRvRj4sIV0Ho2rQ3C", None),
        ("Strength & Warmth Modulation", "1NuBeWKNO2odgRTCacWe1HpifXpUbQI_o",
         "1GxT-tEE4_dNl8NAiuvJPW39-ZGGq2yim", None),
        ("Three Levels of Knowledge", "1PyHteKdK1nQIrPb8VhFVyDjfqJp5JqWA",
         "1iJD52ZmkrnXMFxtMRDDiQaTT3NEID0MO", None),
        ("Trust Metre", "1OtxSqSUdSdA8kc1cUY3n-sm83wXWyB-M",
         "11wCsDlUu8B-P_-j3i9NFWwBG20r7exC2", None),
        ("Empathy", "1tcv4SZS7_Z89WbW2OeEPXn5vj8-KRWu3",
         "16jHckRJKPsfiw5IQoAhpCJ0S0vw53vsc", None),
        ("Influence", "1hY9a2Lo3vs1bEsKWUOm7gPERoaszR73G",
         "1rW6AIxWwVsFOBiCcaFCrfKj-gFmmYnyM", None),
        ("Perceptions", "1Btt3ABLElAC7CqB_fytLnUtxgiHdX6PH",
         "1VMPVg93oEvksScFwT6eWlqVgvwUuXIm4", None),
        ("Indirect Approach", "1NWGBTqasWstKRReJN1hhoU1HLvQlQTZm",
         "1AzX93UoEpcMDeD7psIXHY_gXFgrKtF_Y", None),
        ("Confirmation Bias", "1tAtAQf-S-Fk4Js01tiFlOQCvNTpfmmXJ",
         "1gEgKoctYxzHWbov4kFIOUQvwhrBiv6_n", None),
        ("Accountability", "1k9Rgga-rVsMXk5cwM3ULwgA0cVuX-37X",
         "1_h2VJiYXzbpOLKcO2m2tNuWLo_vqUrc1", "1XUs7NrqmK4077IMCPltGCFH-hKrRDSW3"),
    ]),
    ("Leading Teams", [
        ("Decentralized Command", "17GzY4onQJqYToLX_YostHsERaa2CO7rA",
         "1c3qDfWLcBmCG0Mtk9eJWhx8M1aeHfQqt", None),
        ("Problem Matrix", "1WPr1yNJ3xJYULJ1ALIqv0MVRyREglMkB",
         "1yfoJWA_VNooFPd2DuYSzChPfIg9gYAEg", None),
        ("Lead vs. Lag Indicators", "1BrOqOnRctO6izI11ReCYFUMhm1U114XL",
         "1id_TB0rDmiiiGEBYsK8OXIxxOUgoOPAu", None),
        ("Focus", "1MJ8kyOcHO1oL2sE06V75L6PciLw1aTA5",
         "1rbI4Z6mU4citE_s5VRkFbrfMJVMrv_nx", None),
        ("Innovation", "19T21bq0nkj2mgTk2xeo-anwoQEnRr9wi",
         "11bqDXMyOZj-VK6TZewDlf95HVvojVUR0", None),
        ("Leadership Capital", "1ijSUEFQrH1cu8IV8OqTcvBbFBOn2BcGW",
         "1XRuGVQ1dTCr02qpr7VD0sIIN_age0rjL", None),
        ("Success", "1N_aqSkYl_UeoofNHn9kvJDClF0bA34yE",
         "19KEbp7ZYWJA021tH_Bz7WlLhyk4_nkhG", None),
        ("Axis of Distractions", "1RHISYVUpFq5ChY9MTB0oxjQm5iAGjAOo",
         "1RAutoDCWbyAJcVHAW4RF0UgUqKbmsj6p", None),
        ("Big L Leader", "16ZMW7zqfhF5uA6woQUMhsPdbqhB9zntM",
         "18Mcfv2inbKOT3H6qYWxR0PtgSJBwIwuO", None),
        ("4 Aspects of Change", "1ISsSJjbF9g3yFqrJ2Djn6treDDzgEZ9O",
         "1iznQsm2yzCOLCfyH8eJ9vfS0Hft3raIG", None),
        ("Opportunity Costs", "1reobI6BYPN1eZnu72FM7B2-U3RyhVkCu",
         "1r2AZRm31jd7Ge0cK68sNF4RJ9PvJXUhA", None),
        ("4 Types of Choices", "1VbI5vec-gG1uvWgohmNan7akeWCVEO5I",
         "1N6JhQbUd3iM9rIl71DFjWXx4LKTslcwB", None),
    ]),
    ("Leading Organization", [
        ("VRIN Model", "1J_ibOUbT9JpjznXLxn_P2_JwtgB5wC6o",
         "1rStuUpuDRei6nZ7MKQROUpnUyJV0ITwl", None),
        ("OODA Loop", "1MkzDAj7Db9GFwXVo3kGWXOxETHhPEVra",
         "19ms8LhuKjUHHtZ1tGUo8RjOV_wpmkK1V", None),
        ("Vision Casting", "1VxHVymtxM0WJpSYneiAuT1PtEmlt2rLQ",
         "1_FBOxVrCx9FQIy5N8TfyMPCCnekNhYGz", None),
        ("4 Deep Leadership Model", "19qE5oAalDraI0wblZpxjtDVe35kPSM0K",
         "1fOOaOWWyE3XJgBxCD7eRZI5HfQR-A9T3", None),
        ("Risk Minimization", "1vlI3ctzlfwTrTFIvUmMt8bi7HOugr6uC",
         "1W7f64dJ55SbGOvg1LZD0EItcqdCvaUsP", None),
        ("Attracting, Equipping & Developing Leaders", "1NJ4Aoz__cWHumUQOy1IJ5VyVgmzUs28Q",
         "1hNu4rnBKSvOmicJ4TPbgEJlpkG-ZJGGq", None),
        ("Vision Carriers", "1Widbszc0SBMY6kFeGOrZRKbDQ0kg14o9",
         "1KD9wCY6j1RcHWYJGbJmQWSZPVadbdWta", None),
        ("Power of Regrets", "1TDGAFAyBqWmjdEFVHLlAXucxoLsDwzRD",
         "1W4ndNlpykU6GwG7xBJl8ZKt7uLALGD2a", None),
        ("Burn the Boats", "1x3EUisT5iVgJficTjtWa_0nFtJuDs5u5",
         "1fmOUT4HLU7kQL6XiqsdNZcSLHEPLc7Mx", None),
    ]),
]

MODULE_ORDER = [module for module, _ in COURSE]
